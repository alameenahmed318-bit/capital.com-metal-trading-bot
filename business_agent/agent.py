"""UAE Business AI Agent orchestration engine."""
from __future__ import annotations
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .adapters import TavilyMarketResearch, HubSpotCRM, StripePayments, AgentMailMessenger


@dataclass
class Event:
    ts: float
    kind: str
    message: str
    data: dict[str, Any]


class BusinessAgent:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.state: dict[str, Any] = {
            "status": "ready", "leads": [], "requests": [], "tasks": [], "payments": [],
            "approvals": [], "opportunities": [], "quotes": [], "processed_messages": [],
            "followups": []
        }
        self.events: list[Event] = []
        self._load()
        self.market = TavilyMarketResearch(self.settings.tavily_api_key) if self.settings.tavily_api_key else None
        self.crm = HubSpotCRM(self.settings.hubspot_access_token) if self.settings.hubspot_access_token else None
        self.payments = StripePayments(self.settings.stripe_secret_key) if self.settings.stripe_secret_key else None
        self.mail = AgentMailMessenger(self.settings.agentmail_api_key, self.settings.agentmail_inbox_id) if self.settings.agentmail_api_key else None

    def log(self, kind: str, message: str, **data: Any) -> None:
        e = Event(time.time(), kind, message, data)
        self.events.append(e)
        self.events = self.events[-500:]
        self.state["last_event"] = asdict(e)
        self._save()

    def _load(self) -> None:
        p = Path(self.settings.state_file)
        if p.exists():
            try:
                self.state.update(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass

    def _save(self) -> None:
        p = Path(self.settings.state_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def scan_market(self, queries: list[str]) -> list[dict]:
        if not self.market:
            self.log("market", "Market connector not configured")
            return []
        results = []
        for q in queries:
            try:
                found = self.market.search(q)
                results.extend(found)
                self.log("market", "Market scan completed", query=q, results=len(found))
            except Exception as exc:
                self.log("error", "Market scan failed", query=q, error=str(exc))
        self.state["opportunities"] = results[-100:]
        self._save()
        return results

    def _extract_email(self, sender: str) -> str:
        m = re.search(r"<([^>]+)>", sender or "")
        return m.group(1) if m else (sender or "").strip()

    def _classify_service(self, text: str) -> str:
        t = text.lower()
        if any(x in t for x in ("website", "web site", "موقع", "ويب")):
            return "Website / Web Services"
        if any(x in t for x in ("automation", "automate", "أتمتة", "بوت", "bot", "ai")):
            return "Business Automation / AI"
        if any(x in t for x in ("marketing", "تسويق", "ads", "advertising", "إعلانات")):
            return "Digital Marketing"
        if any(x in t for x in ("lead", "leads", "عملاء", "عملاء محتملين")):
            return "Lead Generation"
        if any(x in t for x in ("research", "بحث", "دراسة", "market")):
            return "Market Research"
        return "Business Services Inquiry"

    def _clean_customer_text(self, text: str) -> str:
        """Remove common quoted-email sections so replies are not treated as fresh requests."""
        text = re.sub(r"(?im)^>.*$", "", text)
        text = re.split(r"(?im)^\s*(?:On .+ wrote:|From: .+|Sent: .+|-----Original Message-----)\s*$", text)[0]
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _find_open_request(self, sender: str, thread_id: str | None) -> dict | None:
        candidates = [r for r in self.state.get("requests", []) if (r.get("contact") or {}).get("email", "").lower() == sender.lower()]
        if thread_id:
            for r in reversed(candidates):
                if r.get("thread_id") == thread_id:
                    return r
        for r in reversed(candidates):
            if r.get("status") not in {"completed", "cancelled"}:
                return r
        return None

    def _classify_customer_reply(self, text: str) -> str:
        t = text.lower()
        if any(x in t for x in ("موافق", "موافقين", "ابدأ", "ابدؤوا", "go ahead", "approved", "approve", "accept", "accepted")):
            return "approved"
        if any(x in t for x in ("غالي", "سعر أقل", "خصم", "تخفيض", "budget", "cheaper", "discount", "too expensive")):
            return "negotiate_price"
        if any(x in t for x in ("تعديل", "تعديلات", "نطاق", "scope", "include", "إضافة", "اضافة", "غيروا")):
            return "negotiate_scope"
        if any(x in t for x in ("سؤال", "استفسار", "كيف", "متى", "when", "what", "question")):
            return "question"
        return "general_reply"

    def _sync_hubspot_request(self, request: dict, status: str, note: str = "") -> None:
        if not self.crm:
            return
        hid = request.get("hubspot_id")
        if not hid:
            try:
                contact = request.get("contact") or {}
                lead = self.crm.create_lead({
                    "firstname": contact.get("first_name", ""),
                    "lastname": contact.get("last_name", ""),
                    "email": contact.get("email", ""),
                    "company": request.get("company", ""),
                    "jobtitle": contact.get("job_title", "")
                })
                hid = lead.get("id")
                request["hubspot_id"] = hid
                self.state.setdefault("leads", []).append(lead)
            except Exception as exc:
                self.log("error", "HubSpot contact create failed", request_id=request["id"], error=str(exc))
                return
        try:
            self.crm.update(hid, {"lifecyclestage": "lead", "hs_lead_status": status})
            request["hubspot_status"] = status
            if note:
                request["last_customer_note"] = note[:1000]
        except Exception as exc:
            self.log("error", "HubSpot contact update failed", request_id=request["id"], error=str(exc))

    def _handle_customer_reply(self, request: dict, quote: dict, body: str) -> None:
        intent = self._classify_customer_reply(body)
        request["last_reply"] = body
        request["last_reply_at"] = time.time()
        request["conversation_state"] = intent
        self._sync_hubspot_request(request, "OPEN", body)

        if intent == "approved":
            request["status"] = "approved_pending_invoice"
            request["approval_at"] = time.time()
            self.request_approval("order", {
                "request_id": request["id"],
                "quote_id": quote["id"],
                "amount_aed": quote["amount_aed"],
                "scope": quote["scope"],
                "customer": request.get("contact", {})
            })
            if self.mail:
                self.mail.send((request.get("contact") or {}).get("email", ""), "تم تسجيل الموافقة", "شكرًا لتأكيدكم. تم تسجيل الموافقة على العرض، وسننتقل للفوترة والتنفيذ بعد اعتماد الإجراء المالي.")
            return

        current = float(quote.get("amount_aed", 0))
        if intent == "negotiate_price":
            proposed = round(max(750.0, current * 0.90) / 100.0) * 100
            reply = f"يمكننا مراجعة السعر إلى {proposed:.0f} درهم إماراتي ضمن النطاق الحالي. إذا كان مناسبًا نثبت النطاق وننتقل للخطوة التالية."
            new_quote = self.prepare_quote(request["id"], proposed, quote["scope"], "عرض تفاوضي غير ملزم؛ يحتاج تأكيد النطاق قبل الفاتورة أو العقد.", {"negotiated_from": quote["id"], "customer_intent": intent})
            request["quote_id"] = new_quote["id"]
            request["status"] = "negotiating"
            if self.mail:
                self.mail.send((request.get("contact") or {}).get("email", ""), "Re: عرض الخدمة", reply)
            new_quote["sent_at"] = time.time()
            new_quote["status"] = "sent"
        elif intent == "negotiate_scope":
            request["status"] = "negotiating"
            reply = "ممكن نعدّل نطاق العمل. أرسلوا الإضافات أو العناصر التي تريدون حذفها، وسنراجع أثرها على السعر والمدة ثم نرسل نسخة محدثة من العرض."
            if self.mail:
                self.mail.send((request.get("contact") or {}).get("email", ""), "Re: عرض الخدمة", reply)
        elif intent == "question":
            request["status"] = "negotiating"
            reply = "أكيد. أرسلوا أسئلتكم أو المتطلبات بالتفصيل، وسنوضح النطاق والسعر ومدة التنفيذ قبل أي التزام."
            if self.mail:
                self.mail.send((request.get("contact") or {}).get("email", ""), "Re: عرض الخدمة", reply)
        else:
            request["status"] = "awaiting_customer"
            reply = "شكرًا لردكم. نقدر نراجع السعر والنطاق معكم للوصول إلى صيغة مناسبة قبل اعتماد الطلب."
            if self.mail:
                self.mail.send((request.get("contact") or {}).get("email", ""), "Re: عرض الخدمة", reply)
        self.log("sales", "Customer reply handled", request_id=request["id"], intent=intent, status=request["status"])

    def process_inbound_mail(self) -> int:
        if not self.mail:
            self.log("mail", "AgentMail connector not configured")
            return 0
        processed = set(self.state.get("processed_messages", []))
        count = 0
        try:
            messages = self.mail.list_messages(limit=50)
        except Exception as exc:
            self.log("error", "AgentMail list failed", error=str(exc))
            return 0
        for meta in messages:
            mid = meta.get("message_id")
            if not mid or mid in processed:
                continue
            try:
                msg = self.mail.get_message(mid)
                sender = self._extract_email(msg.get("from", ""))
                if sender.lower() == self.settings.agentmail_inbox_id.lower():
                    processed.add(mid)
                    continue
                body = self._clean_customer_text(msg.get("extracted_text") or msg.get("text") or msg.get("preview") or "")
                subject = msg.get("subject") or "Business inquiry"
                thread_id = msg.get("thread_id") or meta.get("thread_id")
                req = self._find_open_request(sender, thread_id)
                if req:
                    quote = next((q for q in reversed(self.state.get("quotes", [])) if q.get("id") == req.get("quote_id")), None)
                    if quote:
                        self._handle_customer_reply(req, quote, body)
                    else:
                        req["last_reply"] = body
                        req["conversation_state"] = self._classify_customer_reply(body)
                        req["last_reply_at"] = time.time()
                    self.log("mail", "Customer reply matched to existing request", message_id=mid, request_id=req["id"], sender=sender)
                else:
                    service = self._classify_service(subject + "\n" + body)
                    name = sender.split("@")[0] if sender else "Prospect"
                    req = self.create_request(name, service, body, {"email": sender, "first_name": name})
                    req["source"] = "agentmail"
                    req["message_id"] = mid
                    req["subject"] = subject
                    req["thread_id"] = thread_id
                    self.log("mail", "Inbound customer email processed", message_id=mid, request_id=req["id"], sender=sender, service=service)
                processed.add(mid)
                count += 1
            except Exception as exc:
                self.log("error", "Inbound email processing failed", message_id=mid, error=str(exc))
        self.state["processed_messages"] = list(processed)[-1000:]
        self._save()
        return count

    def create_request(self, company: str, service: str, details: str = "", contact: dict | None = None) -> dict:
        req = {
            "id": f"REQ-{int(time.time()*1000)}", "company": company, "service": service,
            "details": details, "contact": contact or {}, "status": "new", "created_at": time.time()
        }
        self.state["requests"].append(req)
        if self.crm and contact:
            try:
                lead = self.crm.create_lead({
                    "firstname": contact.get("first_name", ""), "lastname": contact.get("last_name", ""),
                    "email": contact.get("email", ""), "company": company, "jobtitle": contact.get("job_title", "")
                })
                req["hubspot_id"] = lead.get("id")
                self.state["leads"].append(lead)
            except Exception as exc:
                self.log("error", "CRM create failed", error=str(exc), request_id=req["id"])
        self.log("request", "New service request", request=req)
        return req

    def _base_quote(self, service: str) -> tuple[float, str]:
        catalog = {
            "Website / Web Services": (2500.0, "تصميم وتطوير موقع إلكتروني للشركة، صفحات أساسية، نموذج تواصل، وتجهيز مبدئي للنشر."),
            "Business Automation / AI": (3500.0, "أتمتة عمليات الأعمال باستخدام حلول AI وأتمتة مخصصة حسب احتياج الشركة."),
            "Digital Marketing": (3000.0, "خطة تسويق رقمي وتشغيل حملات ومتابعة أولية حسب نطاق العمل المتفق عليه."),
            "Lead Generation": (2500.0, "إعداد نظام لتوليد وتنظيم العملاء المحتملين مع تسليم البيانات المتاحة حسب النطاق."),
            "Market Research": (1800.0, "بحث سوق مختصر وتحليل فرص ومنافسين وتوصيات عملية."),
            "Business Services Inquiry": (2000.0, "خدمة أعمال مخصصة؛ السعر تقديري أولي ويحتاج تأكيد النطاق.")
        }
        return catalog.get(service, catalog["Business Services Inquiry"])

    def _dynamic_quote(self, request: dict) -> tuple[float, str, dict]:
        """Estimate price from requested complexity and current market signals.

        Market results inform the estimate; they are not treated as authoritative price lists.
        """
        service = request.get("service", "Business Services Inquiry")
        details = request.get("details", "")
        amount, scope = self._base_quote(service)
        complexity = 1.0
        additions: list[str] = []

        rules = [
            (("ecommerce", "متجر", "دفع", "payment", "online store"), 1.45, "متجر/دفع إلكتروني"),
            (("booking", "حجز", "موعد", "calendar"), 1.20, "حجوزات أو مواعيد"),
            (("multilingual", "عربي وإنجليزي", "لغتين", "متعدد اللغات"), 1.15, "تعدد اللغات"),
            (("app", "تطبيق", "mobile"), 1.35, "تكامل/تطبيق"),
            (("crm", "hubspot", "salesforce"), 1.20, "تكامل CRM"),
            (("api", "تكامل", "integration"), 1.20, "تكاملات خارجية"),
            (("custom", "مخصص", "خاص"), 1.15, "تخصيص إضافي"),
        ]
        lower = details.lower()
        for words, multiplier, label in rules:
            if any(w in lower for w in words):
                complexity *= multiplier
                additions.append(label)

        market_refs = []
        if self.market:
            try:
                query = f"UAE {service} pricing packages 2026 Dubai Abu Dhabi"
                market_refs = self.market.search(query)[:5]
                self.log("market", "Pricing market check completed", service=service, results=len(market_refs))
            except Exception as exc:
                self.log("error", "Pricing market check failed", error=str(exc))

        amount = round((amount * complexity) / 100.0) * 100
        amount = max(750.0, min(amount, 25000.0))
        evidence = [
            {"title": r.get("title"), "url": r.get("url")}
            for r in market_refs if r.get("title") or r.get("url")
        ]
        if additions:
            scope += " يشمل التقدير الحالي أيضًا: " + "، ".join(additions) + "."
        return amount, scope, {"complexity": round(complexity, 2), "market_references": evidence}

    def prepare_quote(self, request_id: str, amount_aed: float, scope: str, terms: str = "", pricing_meta: dict | None = None) -> dict:
        q = {
            "id": f"Q-{int(time.time()*1000)}", "request_id": request_id,
            "amount_aed": float(amount_aed), "scope": scope, "terms": terms,
            "pricing": pricing_meta or {}, "status": "ready_to_send", "created_at": time.time()
        }
        self.state["quotes"].append(q)
        self.log("quote", "Dynamic non-binding quote prepared", quote=q)
        return q

    def _send_customer_reply(self, request: dict, quote: dict) -> bool:
        if not self.mail:
            self.log("mail", "Cannot send offer: AgentMail not configured", request_id=request["id"])
            return False
        email = (request.get("contact") or {}).get("email", "").strip()
        if not email:
            self.log("mail", "Cannot send offer: customer email missing", request_id=request["id"])
            return False

        subject = f"عرض مبدئي من {self.settings.company_name} - {request['service']}"
        body = (
            f"مرحباً {request.get('company','')},\n\n"
            "شكرًا لتواصلكم معنا. راجعنا طلبكم وأعددنا تقديرًا مبدئيًا بناءً على المعلومات الحالية.\n\n"
            f"الخدمة: {request['service']}\n"
            f"السعر المبدئي المقترح: {quote['amount_aed']:.0f} درهم إماراتي\n"
            f"النطاق: {quote['scope']}\n\n"
            "هذا عرض مبدئي غير ملزم. السعر النهائي وموعد التنفيذ يتحددان بعد تأكيد المتطلبات والنطاق. "
            "إذا كان مناسبًا، أرسلوا المتطلبات أو أسئلتكم وسنواصل معكم.\n\n"
            f"تحياتنا،\n{self.settings.company_name}"
        )
        try:
            result = self.mail.send(email, subject, body)
            quote["sent_at"] = time.time()
            quote["status"] = "sent"
            quote["delivery"] = {"message_id": result.get("message_id") or result.get("id")}
            self.log("mail", "Customer offer sent", request_id=request["id"], quote_id=quote["id"], recipient=email)
            return True
        except Exception as exc:
            self.log("error", "Customer offer send failed", request_id=request["id"], error=str(exc))
            return False

    def _schedule_followup(self, request: dict, quote: dict, days: int = 2) -> None:
        follow = {
            "id": f"FU-{int(time.time()*1000)}", "request_id": request["id"], "quote_id": quote["id"],
            "status": "scheduled", "due_at": time.time() + days * 86400, "attempts": 0
        }
        self.state.setdefault("followups", []).append(follow)
        self.log("followup", "Customer follow-up scheduled", followup=follow)

    def handle_pending_requests(self) -> int:
        handled = 0
        for request in self.state.get("requests", []):
            if request.get("status") != "new" or request.get("source") != "agentmail":
                continue
            amount, scope, meta = self._dynamic_quote(request)
            quote = self.prepare_quote(
                request["id"], amount, scope,
                "عرض مبدئي غير ملزم؛ الفاتورة أو العقد لا يتم إنشاؤهما تلقائيًا.",
                meta
            )
            if self._send_customer_reply(request, quote):
                request["status"] = "offer_sent"
                request["quote_id"] = quote["id"]
                request["offer_sent_at"] = time.time()
                self._schedule_followup(request, quote)
                handled += 1
            else:
                request["status"] = "offer_send_failed"
        self._save()
        return handled

    def create_task(self, request_id: str, title: str, instructions: str = "") -> dict:
        task = {
            "id": f"TASK-{int(time.time()*1000)}", "request_id": request_id, "title": title,
            "instructions": instructions, "status": "queued", "created_at": time.time()
        }
        self.state["tasks"].append(task)
        self.log("task", "Task queued", task=task)
        return task

    def request_approval(self, action: str, payload: dict[str, Any]) -> None:
        a = {
            "id": f"APR-{int(time.time()*1000)}", "type": action, "status": "pending",
            "payload": payload, "created_at": time.time()
        }
        self.state["approvals"].append(a)
        self.log("approval", f"Approval required: {action}", approval=a)

    def resolve_approval(self, approval_id: str, decision: str = "approve") -> dict:
        """Resolve a pending order approval and create its invoice."""
        approval = next((a for a in self.state.get("approvals", []) if a.get("id") == approval_id), None)
        if not approval:
            return {"status": "not_found", "approval_id": approval_id}
        if approval.get("status") != "pending":
            return {"status": approval.get("status"), "approval_id": approval_id}
        if decision != "approve":
            approval["status"] = "rejected"
            approval["resolved_at"] = time.time()
            self._save()
            return {"status": "rejected", "approval_id": approval_id}
        approval["status"] = "approved"
        approval["resolved_at"] = time.time()
        payload = approval.get("payload") or {}
        if approval.get("type") != "order":
            self._save()
            return {"status": "approved", "approval_id": approval_id}
        request_id = payload.get("request_id")
        request = next((r for r in self.state.get("requests", []) if r.get("id") == request_id), None)
        if not request:
            approval["status"] = "failed"
            self._save()
            return {"status": "failed", "reason": "request_not_found", "approval_id": approval_id}
        description = f"{request.get('service', 'Business service')} - {payload.get('scope', '')}".strip(" -")
        amount = float(payload.get("amount_aed", 0))
        customer = payload.get("customer") or request.get("contact") or {}
        if self.settings.dry_run:
            invoice = {"status": "dry_run", "amount_aed": amount, "description": description}
        elif not self.payments:
            invoice = {"status": "not_configured", "amount_aed": amount}
        else:
            invoice = self.payments.create_invoice(customer, amount, description)
            self.state["payments"].append(invoice)
            self.log("payment", "Stripe invoice created after approval", request_id=request_id, approval_id=approval_id, invoice_id=invoice.get("id"))
        if invoice.get("status") == "dry_run" or invoice.get("id"):
            request["status"] = "invoice_created" if invoice.get("id") else "invoice_ready_dry_run"
            request["invoice_id"] = invoice.get("id")
            request["invoice_url"] = invoice.get("hosted_invoice_url")
            approval["invoice_id"] = invoice.get("id")
            if invoice.get("hosted_invoice_url") and self.mail:
                self.mail.send((request.get("contact") or {}).get("email", ""), "فاتورة الخدمة", "تم إنشاء الفاتورة. رابط الدفع: " + invoice["hosted_invoice_url"])
        else:
            approval["status"] = "failed"
        self._save()
        return {"status": invoice.get("status"), "approval_id": approval_id, "invoice": invoice}
    def create_invoice_after_approval(self, request_id: str, customer: dict, amount_aed: float, description: str) -> dict:
        if self.settings.approval_required_for_money:
            self.request_approval("invoice", {
                "request_id": request_id, "customer": customer,
                "amount_aed": amount_aed, "description": description
            })
            return {"status": "approval_required"}
        if self.settings.dry_run:
            return {"status": "dry_run", "amount_aed": amount_aed}
        if not self.payments:
            return {"status": "not_configured"}
        inv = self.payments.create_invoice(customer, amount_aed, description)
        self.state["payments"].append(inv)
        self.log("payment", "Stripe invoice created", invoice_id=inv.get("id"))
        return inv

    def reconcile_payments(self) -> int:
        """Refresh Stripe invoice status for recorded invoices."""
        if not self.payments:
            return 0
        changed = 0
        for payment in self.state.get("payments", []):
            invoice_id = payment.get("id")
            if not invoice_id or payment.get("status") == "paid":
                continue
            try:
                latest = self.payments.payment_status(invoice_id)
                old = payment.get("status")
                payment.update({
                    "status": latest.get("status", old),
                    "paid": latest.get("paid", False),
                    "hosted_invoice_url": latest.get("hosted_invoice_url"),
                    "amount_due": latest.get("amount_due"),
                    "currency": latest.get("currency"),
                    "last_checked_at": time.time(),
                })
                if payment.get("paid") and old != "paid":
                    changed += 1
                    request = next((r for r in self.state.get("requests", [])
                                     if r.get("invoice_id") == invoice_id), None)
                    if request:
                        request["status"] = "paid"
                        request["paid_at"] = time.time()
                    self.log("payment", "Stripe invoice marked paid", invoice_id=invoice_id)
            except Exception as exc:
                self.log("error", "Stripe payment status check failed", invoice_id=invoice_id, error=str(exc))
        self._save()
        return changed

    def execute_followups(self) -> int:
        """Send due follow-ups once."""
        if not self.mail:
            return 0
        now = time.time()
        sent = 0
        for follow in self.state.get("followups", []):
            if follow.get("status") != "scheduled" or follow.get("due_at", now + 1) > now:
                continue
            request = next((r for r in self.state.get("requests", []) if r.get("id") == follow.get("request_id")), None)
            quote = next((q for q in self.state.get("quotes", []) if q.get("id") == follow.get("quote_id")), None)
            if not request or not quote:
                follow["status"] = "cancelled"
                continue
            email = (request.get("contact") or {}).get("email", "").strip()
            if not email:
                follow["status"] = "cancelled"
                continue
            try:
                self.mail.send(email, "متابعة العرض", f"مرحباً، نتابع معكم بخصوص عرض {request.get('service','الخدمة')} بقيمة {quote.get('amount_aed', 0):.0f} درهم. إذا كان مناسبًا يمكنكم تأكيد الطلب.")
                follow["status"] = "sent"
                follow["sent_at"] = now
                follow["attempts"] = int(follow.get("attempts", 0)) + 1
                sent += 1
            except Exception as exc:
                follow["attempts"] = int(follow.get("attempts", 0)) + 1
                follow["last_error"] = str(exc)
        self._save()
        return sent

    def status(self) -> dict[str, Any]:
        return {
            "name": self.settings.app_name, "dry_run": self.settings.dry_run,
            "connectors": {
                "market": bool(self.market), "hubspot": bool(self.crm),
                "stripe": bool(self.payments), "agentmail": bool(self.mail)
            },
            "status": self.state.get("status", "ready"),
            "requests": len(self.state["requests"]), "leads": len(self.state["leads"]),
            "tasks": len(self.state["tasks"]), "payments": len(self.state["payments"]),
            "approvals": len(self.state["approvals"]), "opportunities": len(self.state["opportunities"]),
            "quotes": len(self.state["quotes"]), "followups": len(self.state.get("followups", [])), "paid_requests": sum(1 for r in self.state["requests"] if r.get("status") == "paid")
        }


def run_cycle():
    agent = BusinessAgent()
    agent.state["status"] = "running"
    agent.log("system", "Business Agent cycle started", dry_run=agent.settings.dry_run)
    approval_id = os.getenv("APPROVAL_ID", "").strip()
    approval_result = agent.resolve_approval(approval_id, os.getenv("APPROVAL_DECISION", "approve").strip().lower()) if approval_id else None
    inbound = agent.process_inbound_mail()
    offers = agent.handle_pending_requests()
    paid = agent.reconcile_payments()
    followups = agent.execute_followups()
    agent.scan_market([
        "UAE companies needing digital marketing",
        "UAE SMEs needing websites",
        "UAE companies needing business automation",
        "Abu Dhabi Dubai companies needing lead generation"
    ])
    result = agent.status()
    result["inbound_processed"] = inbound
    result["offers_handled"] = offers
    result["payments_reconciled"] = paid
    result["followups_sent"] = followups
    if approval_result is not None:
        result["approval_result"] = approval_result
    agent.log("system", "Business Agent cycle completed", status=result)
    return result


if __name__ == "__main__":
    print(json.dumps(run_cycle(), ensure_ascii=False, indent=2))
