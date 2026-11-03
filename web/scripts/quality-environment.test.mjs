import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,rm,symlink,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {fingerprintFiles,compareEnvironment,validateConfigReferences,validateFontTree,userFontConfigurationPaths} from './quality-environment.mjs';

test('environment fingerprint rejects drift before rendering',async()=>{
 const home='/invented/home';
 const fallback=[join(home,'.fonts'),join(home,'.fonts.conf'),join(home,'.fonts.conf.d'),join(home,'.config/fontconfig/fonts.conf'),join(home,'.config/fontconfig/conf.d'),join(home,'.local/share/fonts')];
 assert.deepEqual(userFontConfigurationPaths(home,{}),fallback);
 assert.deepEqual(userFontConfigurationPaths(home,{XDG_CONFIG_HOME:'',XDG_DATA_HOME:''}),fallback);
 assert.deepEqual(userFontConfigurationPaths(home,{XDG_CONFIG_HOME:'/invented/config',XDG_DATA_HOME:'/invented/data'}),[...fallback.slice(0,3),'/invented/config/fontconfig/fonts.conf','/invented/config/fontconfig/conf.d','/invented/data/fonts']);
 assert.throws(()=>userFontConfigurationPaths(home,{XDG_CONFIG_HOME:'relative-config'}),{code:'ERR_QUALITY_ENV_USER_FONT_CONFIG'});
 assert.throws(()=>userFontConfigurationPaths(home,{XDG_DATA_HOME:'relative-data'}),{code:'ERR_QUALITY_ENV_USER_FONT_CONFIG'});
 const refs=validateConfigReferences('/etc/fonts/fonts.conf','<fontconfig><include ignore_missing="yes">conf.d</include><include>included.data</include><dir>/usr/share/fonts</dir><dir prefix="xdg">fonts</dir><dir>~/.fonts</dir></fontconfig>');
 assert.deepEqual(refs.map(({kind,path})=>[kind,path]),[['include','/etc/fonts/conf.d'],['include','/etc/fonts/included.data'],['dir','/usr/share/fonts'],['user-font-dir','fonts'],['user-font-dir','~/.fonts']]);
 assert.equal(validateConfigReferences('/etc/fonts/conf.d/51-local.conf','<fontconfig><include>local.conf</include></fontconfig>')[0].path,'/etc/fonts/local.conf');
 assert.throws(()=>validateConfigReferences('/etc/fonts/fonts.conf','<fontconfig><include>/tmp/external.data</include></fontconfig>'),{code:'ERR_QUALITY_ENV_CONFIG_REFERENCE'});
 assert.throws(()=>validateConfigReferences('/etc/fonts/fonts.conf','<fontconfig><dir>/home/private/fonts</dir></fontconfig>'),{code:'ERR_QUALITY_ENV_FONT_PATH'});
 assert.equal(validateConfigReferences('/etc/fonts/fonts.conf','<fontconfig><dir>/usr/share/texlive/texmf-dist/fonts/opentype/public/lm</dir><dir>/usr/share/poppler/cMap</dir></fontconfig>').length,2);
 assert.throws(()=>validateConfigReferences('/etc/fonts/fonts.conf','<fontconfig><dir>relative-fonts</dir></fontconfig>'),{code:'ERR_QUALITY_ENV_FONT_PATH'});
 for(const dtd of ['urn:fontconfig:fonts.dtd','fonts.dtd','../fonts.dtd'])assert.deepEqual(validateConfigReferences('/etc/fonts/fonts.conf',`<!DOCTYPE fontconfig SYSTEM "${dtd}"><fontconfig/>`),[]);
 assert.throws(()=>validateConfigReferences('/etc/fonts/fonts.conf','<!DOCTYPE fontconfig SYSTEM "file:///tmp/fonts.dtd"><fontconfig/>'),{code:'ERR_QUALITY_ENV_CONFIG_SYNTAX'});
 assert.throws(()=>validateConfigReferences('/etc/fonts/fonts.conf','<!DOCTYPE fontconfig [<!ENTITY x SYSTEM "file:///private/config">]><fontconfig/>'),{code:'ERR_QUALITY_ENV_CONFIG_SYNTAX'});
 const dir=await mkdtemp(join(tmpdir(),'quality-env-fixture-'));
 try{
  const publicFonts=join(dir,'invented-public-fonts');
  const externalFonts=join(dir,'invented-external-fonts');
  const publicFileFonts=join(dir,'invented-public-file-fonts');
  await Promise.all([mkdir(publicFonts),mkdir(externalFonts),mkdir(publicFileFonts)]);
  await Promise.all([writeFile(join(publicFonts,'invented.ttf'),'invented public font bytes'),writeFile(join(externalFonts,'outside.ttf'),'invented external font bytes'),writeFile(join(publicFileFonts,'inside.ttf'),'invented inside font bytes')]);
  await validateFontTree(publicFonts,[publicFonts]);
  await symlink(externalFonts,join(publicFonts,'external-dir'),'dir');
  await assert.rejects(validateFontTree(publicFonts,[publicFonts]),{code:'ERR_QUALITY_ENV_FONT_PATH'});
  await rm(join(publicFonts,'external-dir'));
  await symlink(join(externalFonts,'outside.ttf'),join(publicFileFonts,'external.ttf'),'file');
  await assert.rejects(validateFontTree(publicFileFonts,[publicFileFonts]),{code:'ERR_QUALITY_ENV_FONT_PATH'});
  const font=join(dir,'invented-font-fixture.ttf');
  const config=join(dir,'invented-font-config.conf');
  const configBytes=Buffer.from('invented config fixture v1');
  const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
  const configFingerprint={bytes:configBytes.length,sha256:digest(configBytes)};
  await writeFile(font,'invented font fixture bytes v1');
  const expected={chromeVersion:'Google Chrome 156.0.8078.47',fonts:await fingerprintFiles([font]),configs:{[config]:configFingerprint}};
  const assertDrift=(actual,code)=>assert.throws(()=>compareEnvironment(expected,actual),{code});

  compareEnvironment(expected,{...expected,fonts:await fingerprintFiles([font])});
  await writeFile(font,'invented font fixture bytes v2');
  assertDrift({...expected,fonts:await fingerprintFiles([font])},'ERR_QUALITY_ENV_FONTS_DRIFT');
  await writeFile(font,'invented font fixture bytes v1');
  compareEnvironment(expected,{...expected,fonts:await fingerprintFiles([font])});

  assertDrift({...expected,chromeVersion:'Google Chrome 156.0.8078.48'},'ERR_QUALITY_ENV_CHROME_DRIFT');
  assertDrift({...expected,fonts:{...expected.fonts,[join(dir,'added-font.ttf')]:{bytes:1,sha256:digest(Buffer.from('x'))}}},'ERR_QUALITY_ENV_FONTS_DRIFT');
  const {[font]:_fontOmitted,...missingFont}=expected.fonts;
  assertDrift({...expected,fonts:missingFont},'ERR_QUALITY_ENV_FONTS_DRIFT');
  assertDrift({...expected,configs:{...expected.configs,[join(dir,'added-config.conf')]:configFingerprint}},'ERR_QUALITY_ENV_CONFIGS_DRIFT');
  assertDrift({...expected,configs:{}},'ERR_QUALITY_ENV_CONFIGS_DRIFT');
  assertDrift({...expected,configs:{[config]:{...configFingerprint,sha256:digest(Buffer.from('invented config fixture v2'))}}},'ERR_QUALITY_ENV_CONFIGS_DRIFT');
  compareEnvironment(expected,{...expected,fonts:await fingerprintFiles([font])});
 }finally{await rm(dir,{recursive:true,force:true});}
});
