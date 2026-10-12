"""Job progress must reach the page even when its live updates (/api/events) are held back, e.g. by antivirus.
Run: uv run --project engine --with playwright python tools/tests/live_updates_test.py block   (or: normal)
Needs test media from engine/tests/media_gen.py in /tmp/uimedia/media.json; CHROMIUM_PATH picks the browser."""
import os,sys,time,json,subprocess,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path('/home/user/studio-pro');TOKEN='ui-test-token'
env={**os.environ,'STUDIO_ENGINE_HOME':'/tmp/sse_home','STUDIO_ENGINE_TOKEN':TOKEN,'STUDIO_BIN':str(ROOT/'desktop/build/bin')}
e=subprocess.Popen(['uv','run','--project',str(ROOT/'engine'),'studio-engine','--app'],env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for _ in range(100):
    try: urllib.request.urlopen('http://127.0.0.1:8765/api/hello',timeout=1);break
    except Exception: time.sleep(0.3)
M=json.loads(Path('/tmp/uimedia/media.json').read_text())
try:
  with sync_playwright() as p:
    b=p.chromium.launch(**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}));pg=b.new_page(bypass_csp=True)
    if sys.argv[1]=='block': pg.route('**/api/events*',lambda r:r.abort())
    pg.on('dialog',lambda d:d.dismiss())
    pg.goto(f'http://127.0.0.1:8765/#engine={TOKEN}')
    pg.wait_for_function("document.querySelector('#engStatus').innerText.includes('Engine connected')",timeout=60000)
    pg.evaluate(f"STU.settings.separate=true;engSetFile('video',{json.dumps(M['video'])});engSetFile('audio',{json.dumps(M['audio'])});engReport()")
    t0=time.time()
    pg.wait_for_function("!document.querySelector('#engReport').innerText.includes('Checking')",timeout=120000)
    print(sys.argv[1],'done in',round(time.time()-t0,1),'s:',pg.inner_text('#engReport')[:200].replace('\n',' | '))
    print('jobs box:',pg.inner_text('#engJobs')[:120].replace('\n',' | '))
finally:
  e.terminate()
