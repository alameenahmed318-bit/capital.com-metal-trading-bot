"""UAE Business AI Agent orchestration engine."""

from __future__ import annotations
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .adapters import TavilyMarketResearch, HubSpotCRM, StripePayments

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
            "status":"ready","leads":[],"requests":[],"tasks":[],"payments":[],
            "approvals":[],"opportunities":[],"quotes":[]
        }
        self.events: list[Event] = []
        self._load()
        self.market = TavilyMarketResearch(self.settings.tavily_api_key) if self.settings.tavily_api_key else None
        self.crm = HubSpotCRM(self.settings.hubspot_access_token) if self.settings.hubspot_access_token else None
        self.payments = StripePayments(self.settings.stripe_secret_key) if self.settings.stripe_secret_key else None

    def log(self, kind: str, message: str, **data: Any) -> None:
        e=Event(time.time(),kind,message,data); self.events.append(e); self.events=self.events[-500:]
        self.state["last_event"]=asdict(e); self._save()

    def _load(self) -> None:
        p=Path(self.settings.state_file)
        if p.exists():
            try: self.state.update(json.loads(p.read_text(encoding="utf-8")))
            except (OSError,json.JSONDecodeError): pass

    def _save(self) -> None:
        p=Path(self.settings.state_file); p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding="utf-8")

    def scan_market(self, queries: list[str]) -> list[dict]:
        if not self.market:
            self.log("market","Market connector not configured"); return []
        results=[]
        for q in queries:
            try:
                found=self.market.search(q); results.extend(found)
                self.log("market","Market scan completed",query=q,results=len(found))
            except Exception as exc: self.log("error","Market scan failed",query=q,error=str(exc))
        self.state["opportunities"]=results[-100:]; self._save(); return results

    def create_request(self, company: str, service: str, details: str="", contact: dict|None=None) -> dict:
        req={"id":f"REQ-{int(time.time()*1000)}","company":company,"service":service,
             "details":details,"contact":contact or {},"status":"new","created_at":time.time()}
        self.state["requests"].append(req)
        if self.crm and contact:
            try:
                lead=self.crm.create_lead({"firstname":contact.get("first_name",""),
                    "lastname":contact.get("last_name",""),"email":contact.get("email",""),
                    "company":company,"jobtitle":contact.get("job_title","")})
                req["hubspot_id"]=lead.get("id"); self.state["leads"].append(lead)
            except Exception as exc: self.log("error","CRM create failed",error=str(exc),request_id=req["id"])
        self.log("request","New service request",request=req); return req

    def prepare_quote(self, request_id: str, amount_aed: float, scope: str, terms: str="") -> dict:
        q={"id":f"Q-{int(time.time()*1000)}","request_id":request_id,"amount_aed":float(amount_aed),
           "scope":scope,"terms":terms,"status":"approval_required" if self.settings.approval_required_for_money else "ready",
           "created_at":time.time()}
        self.state["quotes"].append(q)
        if q["status"]=="approval_required": self.request_approval("quote",q)
        self.log("quote","Quote prepared",quote=q); return q

    def create_task(self, request_id: str, title: str, instructions: str="") -> dict:
        task={"id":f"TASK-{int(time.time()*1000)}","request_id":request_id,"title":title,
              "instructions":instructions,"status":"queued","created_at":time.time()}
        self.state["tasks"].append(task); self.log("task","Task queued",task=task); return task

    def request_approval(self, action: str, payload: dict[str,Any]) -> None:
        a={"id":f"APR-{int(time.time()*1000)}","type":action,"status":"pending","payload":payload,"created_at":time.time()}
        self.state["approvals"].append(a); self.log("approval",f"Approval required: {action}",approval=a)

    def create_invoice_after_approval(self, request_id: str, customer: dict, amount_aed: float, description: str) -> dict:
        if self.settings.approval_required_for_money:
            self.request_approval("invoice",{"request_id":request_id,"customer":customer,
                                             "amount_aed":amount_aed,"description":description})
            return {"status":"approval_required"}
        if self.settings.dry_run: return {"status":"dry_run","amount_aed":amount_aed}
        if not self.payments: return {"status":"not_configured"}
        inv=self.payments.create_invoice(customer,amount_aed,description)
        self.state["payments"].append(inv); self.log("payment","Stripe invoice created",invoice_id=inv.get("id")); return inv

    def status(self) -> dict[str,Any]:
        return {"name":self.settings.app_name,"dry_run":self.settings.dry_run,
                "connectors":{"market":bool(self.market),"hubspot":bool(self.crm),"stripe":bool(self.payments)},
                "status":self.state.get("status","ready"),"requests":len(self.state["requests"]),
                "leads":len(self.state["leads"]),"tasks":len(self.state["tasks"]),
                "payments":len(self.state["payments"]),"approvals":len(self.state["approvals"]),
                "opportunities":len(self.state["opportunities"]),"quotes":len(self.state["quotes"]),
                "last_event":self.state.get("last_event")}

def run_cycle():
    agent=BusinessAgent(); agent.state["status"]="running"
    agent.log("system","Business Agent cycle started",dry_run=agent.settings.dry_run)
    agent.scan_market(["UAE companies needing digital marketing","UAE SMEs needing websites",
                       "UAE companies needing business automation","Abu Dhabi Dubai companies needing lead generation"])
    result=agent.status(); agent.log("system","Business Agent cycle completed",status=result); return result

if __name__=="__main__":
    print(json.dumps(run_cycle(),ensure_ascii=False,indent=2))
