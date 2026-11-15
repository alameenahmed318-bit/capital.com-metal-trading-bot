"""AL-ZOOL client configuration bundles; never distribute proprietary source.

Only owner-held engine code remains on the server. Customers receive MT5
connection settings, not .mq5 code or an unverified compiled executable.
"""
import base64
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[2]
EA_SOURCE=ROOT/"mt5_client"/"ALZOOL_Central_Client.mq5"
CLIENT_SERVER="https://al-zool-central-engine.onrender.com"


def _one_line(value, limit):
    value=str(value or "")
    if len(value)>limit or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError("Invalid onboarding value")
    return value


def mt5_inputs(client_id, client_token, server_url=CLIENT_SERVER):
    ident=_one_line(client_id,128)
    token=_one_line(client_token,200)
    server=_one_line(server_url,200)
    parsed=urlparse(server)
    if not ident or not token or parsed.scheme!="https" or parsed.netloc!="al-zool-central-engine.onrender.com" or parsed.path not in ("","/"):
        raise ValueError("Invalid MT5 onboarding destination")
    if len(token)<20:
        raise ValueError("Invalid client token")
    return ("InpServerUrl="+server+"\r\n"
            "InpOwnerMode=false\r\n"
            "InpOwnerPairingKey=\r\n"
            "InpOwnerConnectionId=\r\n"
            "InpEngineId="+ident+"\r\n"
            "InpEngineToken="+token+"\r\n"
            "InpEnableCentralTrading=false\r\n"
            "InpEnableCentralPositionManagement=false\r\n"
            "InpTradeLots=0.01\r\n"
            "InpMaxCentralActionLots=0.01\r\n"
            "InpMaxCentralTotalLots=0.10\r\n")


def build_bundle(client_id, client_token, email="", password=None, ea_source=None):
    """Return ZIP, never store credentials on disk. No order entry is made."""
    inputs=mt5_inputs(client_id,client_token)
    # Kept as optional argument for backward compatibility; NEVER ship mq5 bytes.
    # Compiled .ex5 must be supplied separately after owner-controlled build.
    if ea_source is not None:
        raise ValueError("EA source distribution is disabled")
    email=_one_line(email,254)
    if password is not None:
        password=_one_line(password,256)
    instructions=(
        "AL-ZOOL | MT5 CLIENT SETUP (Windows)\n"
        "1) Obtain the separately supplied, owner-approved AL-ZOOL .ex5 binary.\n"
        "2) Copy it to MT5 > File > Open Data Folder > MQL5 > Experts.\n"
        "3) No .mq5 SOURCE or .ex5 executable is included in this settings ZIP.\n"
        "4) In MT5, Tools > Options > Expert Advisors: allow WebRequest for:\n"
        "   "+CLIENT_SERVER+"\n"
        "5) Attach ALZOOL_Central_Client to the XAUUSD M1 chart.\n"
        "6) In Inputs click Load and select AL-ZOOL-Client-MT5.set.\n"
        "7) Check InpOwnerMode=false and verify Engine ID/Token are filled.\n"
        "8) Central trading and central position management are OFF by default.\n"
        "9) Test on a DEMO account. Do not enable trading without reviewing risk and stop-loss.\n"
        "10) Never share the .set file or connection token with a third party.\n"
        "\nArabic / عربي:\n"
        "هذه حزمة إعدادات الاتصال فقط. استلم ملف EX5 المعتمد من المالك ثم حمّل ملف set من Inputs.\n"
        "التداول الآلي معطّل افتراضياً، واختبار الحساب التجريبي مطلوب قبل أي تفعيل.\n"
        "تنزيل حزمة جديدة من بوابة العميل يغيّر رمز الاتصال ويبطل الحزمة القديمة.\n"
    )
    settings=("server_url="+CLIENT_SERVER+"\n"
              "client_email="+email+"\n"
              "client_id="+_one_line(client_id,128)+"\n"
              "client_token="+_one_line(client_token,200)+"\n"
              "owner_mode=false\n"
              "central_trading=false\n")
    if password is not None:
        settings+="initial_login_password="+password+"\n"
    archive=BytesIO()
    with ZipFile(archive,"w",ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr("AL-ZOOL-Client/AL-ZOOL-Client-MT5.set",inputs)
        z.writestr("AL-ZOOL-Client/START_HERE_AR.txt",instructions)
        z.writestr("AL-ZOOL-Client/PRIVATE_CONNECTION.txt",settings)
    return {
        "filename":"AL-ZOOL-Client-MT5-Settings.zip",
        "package_base64":base64.b64encode(archive.getvalue()).decode("ascii"),
        "format":"SET_ONLY_PRIVATE_NO_SOURCE",
        "compiled_ex5_included":False,
        "trading_enabled_by_package":False,
    }
