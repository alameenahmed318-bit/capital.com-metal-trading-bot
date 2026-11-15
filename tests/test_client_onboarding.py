"""AL-ZOOL client package is safe, authentic and never starts trading."""
import base64
import io
import pathlib
import sys
import unittest
from zipfile import ZipFile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from client_onboarding import build_bundle,mt5_inputs,EA_SOURCE

class ClientOnboardingTests(unittest.TestCase):
    def test_real_ea_source_exists(self):
        self.assertTrue(EA_SOURCE.is_file())
        self.assertIn(b'InpEngineId',EA_SOURCE.read_bytes())

    def test_mt5_set_is_client_only_and_execution_off(self):
        preset=mt5_inputs("client123456789","token-abcdefghijklmnopqrstuvwxyz")
        self.assertIn("InpOwnerMode=false",preset)
        self.assertIn("InpEnableCentralTrading=false",preset)
        self.assertIn("InpEnableCentralPositionManagement=false",preset)
        self.assertIn("InpEngineId=client123456789",preset)
        self.assertIn("InpEngineToken=token-abcdefghijklmnopqrstuvwxyz",preset)
        self.assertIn("\r\n",preset)
        self.assertNotIn("InpOwnerMode=true",preset)

    def test_bundle_only_contains_preset_instructions_and_credentials(self):
        package=build_bundle("client123456789","token-abcdefghijklmnopqrstuvwxyz",
                             email="example@example.com",password="temporary-test-pw")
        self.assertEqual(package["format"],"SET_ONLY_PRIVATE_NO_SOURCE")
        self.assertFalse(package["compiled_ex5_included"])
        self.assertFalse(package["trading_enabled_by_package"])
        with ZipFile(io.BytesIO(base64.b64decode(package["package_base64"]))) as z:
            names=z.namelist()
            self.assertFalse(any(x.endswith(".mq5") for x in names))
            self.assertTrue(any(x.endswith(".set") for x in names))
            self.assertFalse(any(x.endswith(".ex5") for x in names))
            preset=z.read("AL-ZOOL-Client/AL-ZOOL-Client-MT5.set").decode()
            self.assertIn("InpEngineId=client123456789",preset)
            self.assertIn("InpEnableCentralTrading=false",preset)
            self.assertIn("InpEngineToken=token-abcdefghijklmnopqrstuvwxyz",preset)
            details=z.read("AL-ZOOL-Client/PRIVATE_CONNECTION.txt").decode()
            self.assertIn("initial_login_password=temporary-test-pw",details)

    def test_client_download_does_not_leak_admin_password(self):
        package=build_bundle("client123456789","token-abcdefghijklmnopqrstuvwxyz",email="example@example.com")
        with ZipFile(io.BytesIO(base64.b64decode(package["package_base64"]))) as z:
            details=z.read("AL-ZOOL-Client/PRIVATE_CONNECTION.txt").decode()
        self.assertNotIn("initial_login_password",details)

    def test_reject_invalid_server_and_linebreak_injection(self):
        with self.assertRaises(ValueError):
            mt5_inputs("abc123456789","token-abcdefghijklmnopqrstuvwxyz","http://malicious.example")
        with self.assertRaises(ValueError):
            mt5_inputs("abc123456789\nInpOwnerMode=true","token-abcdefghijklmnopqrstuvwxyz")

    def test_client_portal_has_secure_bundle_download_and_recovery_screen(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        html=(root/"index.html").read_text(encoding="utf-8")
        js=(root/"app.js").read_text(encoding="utf-8")
        server=(root/"marketplace"/"backend"/"app.py").read_text(encoding="utf-8")
        self.assertIn('id="clientPackageDownload"',html)
        self.assertIn('id="resetPasswordView"',html)
        self.assertIn('if path=="/api/client/onboarding-package"',server)
        self.assertIn('if banned(admin_user(u["id"])):',server)
        self.assertIn('credentials=issue_license(u["id"])',server)
        self.assertIn('await api("/api/client/onboarding-package","POST",{})',js)
        self.assertIn("PASSWORD_RECOVERY",js)
        self.assertIn("welcome_email_status",server)
        self.assertIn('msg("clientPackageStatus"',js)

if __name__=="__main__":
    unittest.main()
