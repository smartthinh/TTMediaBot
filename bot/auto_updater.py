import wx
import urllib.request
import json
import threading
import sys
import os
import subprocess
from bot.app_vars import app_version

GITHUB_API_URL = "https://api.github.com/repos/smartthinh/TTMediaBot/releases/latest"
CHANGELOG_URL = "https://raw.githubusercontent.com/smartthinh/TTMediaBot/master/CHANGELOG.md"

def check_for_updates():
    try:
        req = urllib.request.Request(GITHUB_API_URL, headers={'User-Agent': 'TTMediaBot-AutoUpdater'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            latest_version = data.get("tag_name", "").lstrip("v")
            
            if is_newer_version(app_version, latest_version):
                assets = data.get("assets", [])
                download_url = None
                for asset in assets:
                    if asset.get("name", "").endswith(".exe"):
                        download_url = asset.get("browser_download_url")
                        break
                
                if download_url:
                    changelog_text = ""
                    try:
                        url = CHANGELOG_URL
                        if "github.com" in url and "/blob/" in url:
                            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                        cl_req = urllib.request.Request(url, headers={'User-Agent': 'TTMediaBot-AutoUpdater'})
                        with urllib.request.urlopen(cl_req, timeout=5) as cl_res:
                            changelog_text = cl_res.read().decode('utf-8', errors='ignore')
                    except Exception as e:
                        print(f"Error fetching remote changelog: {e}")
                        if os.path.exists("CHANGELOG.md"):
                            try:
                                with open("CHANGELOG.md", "r", encoding="utf-8") as f:
                                    changelog_text = f.read()
                            except Exception:
                                pass
                        
                    return latest_version, download_url, changelog_text
    except Exception as e:
        print(f"Error checking for updates: {e}")
    return None, None, None

def is_newer_version(current, latest):
    def parse_version(v):
        return [int(x) for x in v.split(".") if x.isdigit()]
    try:
        return parse_version(latest) > parse_version(current)
    except Exception:
        return False

class UpdateDialog(wx.Dialog):
    def __init__(self, parent, latest_version, download_url, changelog_text, translate):
        super().__init__(parent, title=translate("Update Available"), size=(600, 450))
        self.download_url = download_url
        self.latest_version = latest_version
        self.translate = translate
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        info_text = self.translate("A new version of TTMediaBot (v{version}) is available.\
Would you like to update now?").format(version=latest_version)
        self.lbl_info = wx.StaticText(self, label=info_text)
        sizer.Add(self.lbl_info, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 15)
        
        # Read-only multiline textbox for changelog
        self.txt_changelog = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        if changelog_text:
            self.txt_changelog.SetValue(changelog_text)
        else:
            self.txt_changelog.SetValue(self.translate("Release notes are not available."))
        sizer.Add(self.txt_changelog, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        
        self.progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL)
        self.progress.Hide()
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 15)
        
        self.btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_update = wx.Button(self, label=self.translate("Update"))
        self.btn_cancel = wx.Button(self, label=self.translate("Cancel"))
        
        self.btn_update.Bind(wx.EVT_BUTTON, self.on_update)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        
        self.btn_sizer.Add(self.btn_update, 0, wx.RIGHT, 10)
        self.btn_sizer.Add(self.btn_cancel, 0, wx.LEFT, 10)
        sizer.Add(self.btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 15)
        
        self.SetSizer(sizer)
        self.CenterOnScreen()

    def on_cancel(self, event):
        self.EndModal(wx.ID_CANCEL)
        
    def on_update(self, event):
        self.btn_update.Disable()
        self.btn_cancel.Disable()
        self.lbl_info.SetLabel(self.translate("Downloading update... Please wait."))
        self.progress.Show()
        self.Layout()
        
        threading.Thread(target=self.download_update, daemon=True).start()

    def download_update(self):
        try:
            exe_path = os.path.join(os.environ.get("TEMP", "."), f"Setup_TTMediaBot_v{self.latest_version}.exe")
            
            req = urllib.request.Request(self.download_url, headers={'User-Agent': 'TTMediaBot-AutoUpdater'})
            with urllib.request.urlopen(req) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                chunk_size = 65536
                last_percent = -1
                with open(exe_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            if percent != last_percent:
                                last_percent = percent
                                wx.CallAfter(self.progress.SetValue, percent)
            
            wx.CallAfter(self.lbl_info.SetLabel, self.translate("Installing update... Please wait."))
            wx.CallAfter(self.install_and_restart, exe_path)
            
        except Exception as e:
            wx.CallAfter(self.show_error, str(e))

    def show_error(self, err_msg):
        wx.MessageBox(self.translate("Failed to download update:") + f" {err_msg}", self.translate("Update Error"), wx.OK | wx.ICON_ERROR)
        self.EndModal(wx.ID_CANCEL)

    def install_and_restart(self, exe_path):
        bat_path = os.path.join(os.environ.get("TEMP", "."), "ttmediabot_update.bat")
        
        # Locate target executable to launch after installation
        if getattr(sys, 'frozen', False):
            target_exe = sys.executable
        else:
            candidates = [
                os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "TTMediaBot", "TTMediaBot.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "TTMediaBot", "TTMediaBot.exe"),
                os.path.abspath("dist\\TTMediaBot\\TTMediaBot.exe"),
            ]
            target_exe = next((c for c in candidates if os.path.exists(c)), "")

        bat_lines = [
            "@echo off",
            "timeout /t 2 /nobreak >nul",
            f'start /wait "" "{exe_path}" /VERYSILENT /SUPPRESSMSGBOXES /FORCECLOSEAPPLICATIONS /SP-',
            f'if exist "{exe_path}" del /f /q "{exe_path}"',
        ]
        if target_exe:
            bat_lines.append(f'if exist "{target_exe}" start "" "{target_exe}"')
        bat_lines.append('(goto) 2>nul & del "%~f0"')
        bat_lines.append("")

        with open(bat_path, "w", encoding="utf-8") as f:
            f.write("\n".join(bat_lines))
            
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=CREATE_NO_WINDOW)
        sys.exit(0)

def run_auto_updater(translate_func, parent=None, manual=False):
    latest_version, download_url, changelog_text = check_for_updates()
    if latest_version and download_url:
        dlg = UpdateDialog(parent, latest_version, download_url, changelog_text, translate_func)
        dlg.ShowModal()
        dlg.Destroy()
    elif manual:
        wx.MessageBox(
            translate_func("You are using the latest version of TTMediaBot (v{version}).").format(version=app_version),
            translate_func("Update Check"),
            wx.OK | wx.ICON_INFORMATION,
            parent
        )
