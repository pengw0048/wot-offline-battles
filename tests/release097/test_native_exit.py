from pathlib import Path
import ctypes, os, shutil, subprocess, time
W=Path(__file__).resolve().parent
root=Path(os.environ['WOT_NATIVE_TEST_ROOT']);root.mkdir(parents=True,exist_ok=True)
starter=W.parents[1]/'native/offline_worker_starter.exe'
old=Path(os.environ['WOT_BASELINE_STARTER'])
fixture=Path(os.environ['WOT_EXIT_FIXTURE_EXE'])
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
kernel.CloseHandle.argtypes=[ctypes.c_void_p]
def alive(pid):
    h=kernel.OpenProcess(0x100000,False,pid)
    if not h:return False
    try:return kernel.WaitForSingleObject(h,0)==258
    finally:kernel.CloseHandle(h)
def await_file(p):
    end=time.monotonic()+4
    while not p.exists() and time.monotonic()<end:time.sleep(.02)
    assert p.exists(),str(p)
    return int(p.read_text())
def case(mode, baseline=False, paired=True):
    d=root/(('old-' if baseline else '')+mode);d.mkdir(exist_ok=True)
    for n,src in [('offline_worker_starter.exe',old if baseline else starter),('WorldOfTanks.exe',fixture),('Helper.exe',fixture)]:shutil.copy2(src,d/n)
    env=dict(os.environ,WOT_EXIT_FIXTURE=mode)
    start=time.monotonic();p=subprocess.Popen([str(d/'offline_worker_starter.exe'),'--paired-player' if paired else '--player'],cwd=d,env=env,creationflags=0x08000000)
    try:
        pid=await_file(d/'parent-ready')
        helper=await_file(d/'helper-ready') if mode in ('helper','crash','recording') else None
        if baseline:
            time.sleep(6)
            assert p.poll() is None,'baseline must reproduce stuck owner'
            print('PASS baseline: completed visible client leaves owner stuck on helper',flush=True)
            return
        if mode in ('handoff','loading','bad'):
            replacement=await_file(d/'replacement-ready') if mode=='handoff' else None
            time.sleep(6)
            assert p.poll() is None,'live client or invalid notification closed early'
            if replacement:
                assert alive(replacement);(d/'release').touch();assert p.wait(timeout=4)==0
            print('PASS',mode,'does not close prematurely',flush=True)
            return
        outside=None
        if mode=='helper':
            outside=subprocess.Popen([str(d/'WorldOfTanks.exe'),'--outside'],cwd=d,creationflags=0x08000000)
            await_file(d/'outside-ready')
        try:
            result=p.wait(timeout=13)
            assert result==(0xc0000005 if mode=='crash' else 0),(mode,result)
            assert not alive(pid)
            if helper:assert not alive(helper)
            if outside:assert outside.poll() is None,'unrelated same-path client killed'
            if mode=='recording':assert (d/'recording-saved').exists()
            assert not list(d.glob('*.finished')),'stale completion marker'
            print('PASS',mode,'exit %.2fs code=%s'%(time.monotonic()-start,result),flush=True)
        finally:
            if outside and outside.poll() is None:outside.terminate();outside.wait(timeout=3)
    finally:
        if p.poll() is None:
            subprocess.run([str(d/'offline_worker_starter.exe'),'--stop-starter',str(p.pid)],cwd=d,timeout=3,creationflags=0x08000000)
            p.wait(timeout=10)
case('helper',baseline=True)
for mode in ('normal','hang','helper','handoff','loading','bad','recording','crash'):case(mode)
case('normal',paired=False)
print('Native Windows lifecycle: 10/10 passed',flush=True)
