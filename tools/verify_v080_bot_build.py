"""Verify source, CPython 2.7 payload and Windows distribution identity."""
from __future__ import print_function
import glob,hashlib,io,json,marshal,os,shutil,sys,tempfile,types,zipfile
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROOF='docs/testing/airfield-build-inputs-20260923.json'

def read(path):
    with open(path,'rb') as stream:return stream.read()
def sha(data):return hashlib.sha256(data).hexdigest()
def load(path):return json.loads(read(path).decode('utf-8'))
def save(path,obj):
    with open(path,'wb') as stream:stream.write((json.dumps(obj,sort_keys=True,indent=2)+'\n').encode('utf-8'))
def normalized(value):
    if isinstance(value,types.CodeType):
        return tuple(getattr(value,k) for k in ('co_argcount','co_nlocals','co_stacksize','co_flags',
            'co_code','co_names','co_varnames','co_freevars','co_cellvars','co_filename',
            'co_firstlineno','co_lnotab'))+(tuple(normalized(x) for x in value.co_consts),)
    if isinstance(value,tuple):return tuple(normalized(x) for x in value)
    return value

def bytecode():
    assert sys.version_info[:2]==(2,7),sys.version
    proof=load(os.path.join(ROOT,PROOF))
    packages=glob.glob(os.path.join(ROOT,'dist','*.wotmod'));assert len(packages)==1
    package=packages[0];verified=[]
    temp=tempfile.mkdtemp(prefix='bot080-pyc-')
    try:
        with zipfile.ZipFile(package) as z:
            assert z.testzip() is None
            for name in z.namelist():
                assert not name.startswith('/') and '..' not in name.split('/')
                assert not name.endswith('.py')
            for path,expected in sorted(proof['runtime_source_sha256_lf'].items()):
                data=read(os.path.join(ROOT,*path.split('/'))).replace(b'\r\n',b'\n')
                assert sha(data)==expected,path
                name=path[len('src/'):]+'c';pyc=z.read(name)
                assert pyc[:4]==b'\x03\xf3\r\n',name
                actual=marshal.loads(pyc[8:])
                assert normalized(actual)==normalized(compile(data,actual.co_filename,'exec',0,True)),name
                verified.append({'module':name,'pyc_sha256':sha(pyc),'source_sha256_lf':expected})
            z.extractall(temp)
        os.environ['BOT080_CLIENT_SOURCE']=os.path.join(temp,'res','scripts','client')
        os.environ['BOT080_TEST_REPORT']=os.path.join(ROOT,'dist','bot080-bytecode-tests.json')
        sys.path.insert(0,os.path.join(ROOT,'tools'))
        import test_v080_bot_behavior as tests
        assert tests.run_suite(),'compiled v080 rollback contracts failed'
        assert tests.navmod.__file__.startswith(temp),tests.navmod.__file__
        assert tests.sensor.__file__.startswith(temp),tests.sensor.__file__
        result={'python':sys.version,'verified_modules':verified,'wotmod_sha256':sha(read(package)),
            'compiled_tests':tests.RESULTS,'native_gameplay_tested':False}
        save(os.path.join(ROOT,'dist','bot080-bytecode-verification.json'),result)
        print('PASS: %d exact Python 2.7 modules and compiled rollback tests'%len(verified))
    finally:shutil.rmtree(temp)

def distribution(app):
    app=os.path.abspath(app);payload=os.path.join(app,'_internal')
    proof=load(os.path.join(ROOT,PROOF));compiled=load(os.path.join(ROOT,'dist','bot080-bytecode-verification.json'))
    exe=os.path.join(app,'wot-0.9.22-offline-battles.exe');assert read(exe)[:2]==b'MZ'
    config='mods/configs/offline_lan_0922/'
    with zipfile.ZipFile(os.path.join(payload,'client','0.9.22.zip')) as client:
        assert client.testzip() is None
        manifest=load(os.path.join(ROOT,'navgraphs','manifest.json'))
        assert client.read(config+'navgraphs/manifest.json')==read(os.path.join(ROOT,'navgraphs','manifest.json'))
        assert len(manifest['maps'])==41
        for rec in manifest['maps']:
            assert sha(client.read(config+'navgraphs/'+rec['file']))==rec['sha256'],rec
        assert sha(client.read(config+'navgraphs/31_airfield.json'))=='5c981f89f2ab2f36058cf8835e3ea9a666587d15521227a2e121e02c5d0a737f'
        mods=[name for name in client.namelist() if name.endswith('.wotmod')];assert len(mods)==1
        data=client.read(mods[0]);assert sha(data)==compiled['wotmod_sha256']
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for rec in compiled['verified_modules']:
                assert sha(z.read(rec['module']))==rec['pyc_sha256']
        identity=json.loads(client.read(config+'build_identity.json').decode('utf-8'))
        assert identity['buildIdentity']==os.environ['WOT_OFFLINE_BUILD_IDENTITY']
    for path,expected in proof['runtime_source_sha256_lf'].items():
        assert sha(read(os.path.join(payload,'servers','0.9.22',*path.split('/'))).replace(b'\r\n',b'\n'))==expected,path
    sys.path.insert(0,os.path.join(ROOT,'launcher'))
    import core
    fake=tempfile.mkdtemp(prefix='bot080-install-')
    try:
        with open(os.path.join(fake,'WorldOfTanks.exe'),'wb') as stream:stream.write(b'')
        with open(os.path.join(fake,'version.xml'),'wb') as stream:stream.write(b'<version> v.0.9.22.0.1 #1513 </version>')
        assert core.inspect_game_root(fake)['client']==core.PORT_0_9_22
        core.install_client_mod(fake,core.PORT_0_9_22,base_dir=payload)
        assert core._installation_complete(fake,core.PORT_0_9_22,core._CLIENT_INSTALL[core.PORT_0_9_22])
    finally:shutil.rmtree(fake)
    result={'source_commit':os.environ['BOT080_SOURCE_SHA'],'github_run_id':os.environ['GITHUB_RUN_ID'],
        'build_identity':identity,'verified_graphs':41,'base_runtime':'beb750bcc368bb4279959b53ef5aa4022158cba7',
        'source_provenance':proof,'bytecode_verification':compiled,'fake_install_passed':True,
        'all_maps_rebaked':False,'native_gameplay_tested':False}
    save(os.path.join(app,'BOT080_BUILD_EVIDENCE.json'),result)
    print('PASS: exact compiled payload, 139 sources, unchanged 41 graph assets and fake install')

if __name__=='__main__':
    if len(sys.argv)==2 and sys.argv[1]=='bytecode':bytecode()
    elif len(sys.argv)==3 and sys.argv[1]=='distribution':distribution(sys.argv[2])
    else:raise SystemExit('Usage: verify_immutable_build.py bytecode | distribution APP_ROOT')
