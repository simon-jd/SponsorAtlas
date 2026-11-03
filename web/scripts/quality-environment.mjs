import {createReadStream} from 'node:fs';
import {lstat,realpath,readdir,readFile,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile as execFileCallback} from 'node:child_process';
import {promisify} from 'node:util';
import {homedir} from 'node:os';
import {dirname,isAbsolute,join,resolve,sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const execFile=promisify(execFileCallback);
const MAX_COMMAND_OUTPUT=8*1024*1024;
const COMMAND_TIMEOUT_MS=5000;
const MAX_PATHS=50000;
const MAX_CONFIGS=8192;
const MAX_CONFIG_DEPTH=32;
const MAX_CONFIG_BYTES=1024*1024;
const MAX_CONFIG_TOTAL_BYTES=8*1024*1024;
const MAX_FONT_TREE_DEPTH=32;
const MAX_FONT_TREE_ENTRIES=50000;
const CONFIG_ROOTS=['/etc/fonts','/usr/share/fontconfig'];
const FONT_ROOTS=['/usr/share/fonts','/usr/local/share/fonts','/usr/share/texmf/fonts','/usr/share/texlive/texmf-dist/fonts','/usr/share/poppler/cMap'];
const isWithin=(path,root)=>path===root||path.startsWith(root+sep);
const fail=code=>{const error=new Error(code);error.code=code;throw error;};

async function run(command,args){
 try{return await execFile(command,args,{encoding:'utf8',timeout:COMMAND_TIMEOUT_MS,killSignal:'SIGTERM',maxBuffer:MAX_COMMAND_OUTPUT,windowsHide:true});}
 catch{fail('ERR_QUALITY_ENV_COMMAND');}
}

function sameStat(a,b){return a.dev===b.dev&&a.ino===b.ino&&a.size===b.size&&a.mtimeNs===b.mtimeNs&&a.ctimeNs===b.ctimeNs;}

export async function fingerprintFiles(paths){
 if(!Array.isArray(paths)||paths.length>MAX_PATHS)fail('ERR_QUALITY_ENV_PATHS');
 const fingerprints={};
 for(const item of paths){
  if(typeof item!=='string'||!item||!isAbsolute(item))fail('ERR_QUALITY_ENV_PATHS');
  const path=resolve(item);
  if(Object.hasOwn(fingerprints,path))continue;
  const target=await realpath(path);
  const before=await stat(target,{bigint:true});
  if(!before.isFile())fail('ERR_QUALITY_ENV_FILE');
  const hash=createHash('sha256');let bytes=0n;
  for await(const chunk of createReadStream(target)){hash.update(chunk);bytes+=BigInt(chunk.length);}
  const after=await stat(target,{bigint:true});
  if(!sameStat(before,after)||bytes!==before.size||bytes>BigInt(Number.MAX_SAFE_INTEGER)||await realpath(path)!==target)fail('ERR_QUALITY_ENV_UNSTABLE_FILE');
  fingerprints[path]={bytes:Number(bytes),sha256:hash.digest('hex')};
 }
 return fingerprints;
}

function validFileMap(value){
 if(!value||typeof value!=='object'||Array.isArray(value))return false;
 const keys=Object.keys(value);if(keys.length>MAX_PATHS)return false;
 return keys.every(path=>isAbsolute(path)&&value[path]&&typeof value[path]==='object'&&!Array.isArray(value[path])&&Number.isSafeInteger(value[path].bytes)&&value[path].bytes>=0&&typeof value[path].sha256==='string'&&/^[a-f0-9]{64}$/.test(value[path].sha256)&&Object.keys(value[path]).sort().join(',')==='bytes,sha256');
}

function filesEqual(left,right){
 const a=Object.keys(left).sort(),b=Object.keys(right).sort();
 return a.length===b.length&&a.every((path,index)=>path===b[index]&&left[path].bytes===right[path].bytes&&left[path].sha256===right[path].sha256);
}

export function compareEnvironment(expected,actual){
 for(const value of [expected,actual])if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='chromeVersion,configs,fonts'||typeof value.chromeVersion!=='string'||!value.chromeVersion||!validFileMap(value.fonts)||!validFileMap(value.configs))fail('ERR_QUALITY_ENV_MANIFEST');
 if(expected.chromeVersion!==actual.chromeVersion)fail('ERR_QUALITY_ENV_CHROME_DRIFT');
 if(!filesEqual(expected.fonts,actual.fonts))fail('ERR_QUALITY_ENV_FONTS_DRIFT');
 if(!filesEqual(expected.configs,actual.configs))fail('ERR_QUALITY_ENV_CONFIGS_DRIFT');
}

function resolveConfigReference(sourcePath,kind,attributes,rawPath){
 const value=rawPath.trim();
 if(!value||value.includes('\\')||/[\u0000-\u001f]/.test(value)||/[${}]/.test(value))fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
 if(kind==='include'&&attributes.prefix==='xdg'){
  if(value==='fontconfig/fonts.conf'||value==='fontconfig/conf.d')return {kind:'user-config',path:value};
  fail('ERR_QUALITY_ENV_CONFIG_REFERENCE');
 }
 if(kind==='dir'&&attributes.prefix==='xdg'){
  if(value==='fonts')return {kind:'user-font-dir',path:value};
  fail('ERR_QUALITY_ENV_FONT_PATH');
 }
 if(kind==='dir'&&(value==='~/.fonts'||value==='~/.fonts/'))return {kind:'user-font-dir',path:value};
 if(value.startsWith('~'))fail(kind==='dir'?'ERR_QUALITY_ENV_FONT_PATH':'ERR_QUALITY_ENV_CONFIG_REFERENCE');
 if(kind==='dir'&&!isAbsolute(value))fail('ERR_QUALITY_ENV_FONT_PATH');
 const path=kind==='include'&&!isAbsolute(value)?resolve('/etc/fonts',value):resolve(value);
 const roots=kind==='dir'?FONT_ROOTS:CONFIG_ROOTS;
 if(!roots.some(root=>isWithin(path,root)))fail(kind==='dir'?'ERR_QUALITY_ENV_FONT_PATH':'ERR_QUALITY_ENV_CONFIG_REFERENCE');
 return {kind,path};
}

export function userFontConfigurationPaths(home,env={}){
 if(typeof home!=='string'||!isAbsolute(home)||!env||typeof env!=='object')fail('ERR_QUALITY_ENV_USER_FONT_CONFIG');
 const effective=(value,fallback)=>{
  if(value===undefined||value==='')return fallback;
  if(typeof value!=='string'||!isAbsolute(value))fail('ERR_QUALITY_ENV_USER_FONT_CONFIG');
  return resolve(value);
 };
 const configHome=effective(env.XDG_CONFIG_HOME,join(home,'.config'));
 const dataHome=effective(env.XDG_DATA_HOME,join(home,'.local/share'));
 return [...new Set([join(home,'.fonts'),join(home,'.fonts.conf'),join(home,'.fonts.conf.d'),join(configHome,'fontconfig/fonts.conf'),join(configHome,'fontconfig/conf.d'),join(dataHome,'fonts')])];
}

export function validateConfigReferences(sourcePath,text){
 if(typeof sourcePath!=='string'||!isAbsolute(sourcePath)||!CONFIG_ROOTS.some(root=>isWithin(resolve(sourcePath),root))||typeof text!=='string'||Buffer.byteLength(text)>MAX_CONFIG_BYTES)fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
 let xml=text.replace(/<!--[\s\S]*?-->/g,'');
 if(xml.includes('<!--'))fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
 xml=xml.replace(/^\s*<\?xml\s+version=["']1\.0["'](?:\s+encoding=["'][A-Za-z0-9._-]+["'])?\s*\?>/i,'');
 xml=xml.replace(/<!DOCTYPE\s+fontconfig\s+SYSTEM\s+["'](?:urn:fontconfig:fonts\.dtd|fonts\.dtd|\.\.\/fonts\.dtd)["']\s*>/i,'');
 if(/<!|<\?|&|<\s*remap-dir\b/i.test(xml))fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
 const references=[];
 for(const kind of ['include','dir']){
  const pattern=new RegExp(`<${kind}\\b([^>]*)>([\\s\\S]*?)<\\/${kind}\\s*>`,'g');
  xml=xml.replace(pattern,(_match,rawAttributes,rawPath)=>{
   const attributes={};let remaining=rawAttributes;
   remaining=remaining.replace(/([a-z_]+)="([^"]*)"/g,(_attribute,key,value)=>{
    if(Object.hasOwn(attributes,key))fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
    attributes[key]=value;return '';
   });
   if(remaining.trim())fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
   const permitted=kind==='include'?new Set(['ignore_missing','prefix']):new Set(['prefix']);
   if(Object.keys(attributes).some(key=>!permitted.has(key))||(attributes.ignore_missing!==undefined&&!['yes','no'].includes(attributes.ignore_missing))||(attributes.prefix!==undefined&&attributes.prefix!=='xdg'))fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
   const reference=resolveConfigReference(sourcePath,kind,attributes,rawPath);
   if(kind==='include')reference.optional=attributes.ignore_missing==='yes';
   references.push(reference);return '';
  });
 }
 if(/<\/?(?:include|dir)\b/i.test(xml))fail('ERR_QUALITY_ENV_CONFIG_SYNTAX');
 return references;
}

async function rejectUserFontConfiguration(){
 const home=process.env.HOME||homedir();
 if(process.env.FONTCONFIG_FILE||process.env.FONTCONFIG_PATH||process.env.FONTCONFIG_SYSROOT)fail('ERR_QUALITY_ENV_USER_FONT_CONFIG');
 for(const path of userFontConfigurationPaths(home,process.env)){
  try{await lstat(path);}
  catch(error){if(error?.code==='ENOENT')continue;throw error;}
  fail('ERR_QUALITY_ENV_USER_FONT_CONFIG');
 }
}

async function stableConfigText(path,target){
 const before=await stat(target,{bigint:true});
 if(!before.isFile()||before.size>BigInt(MAX_CONFIG_BYTES))fail('ERR_QUALITY_ENV_CONFIG');
 const text=await readFile(target,'utf8');
 const after=await stat(target,{bigint:true});
 if(!sameStat(before,after)||Buffer.byteLength(text)!==Number(before.size)||await realpath(path)!==target)fail('ERR_QUALITY_ENV_UNSTABLE_FILE');
 return text;
}

async function scanFontDirectory(path,allowedRoots,state,depth){
 if(depth>MAX_FONT_TREE_DEPTH)fail('ERR_QUALITY_ENV_FONT_TREE_LIMIT');
 let target;
 try{target=await realpath(path);}catch{fail('ERR_QUALITY_ENV_FONT_PATH');}
 if(!allowedRoots.some(root=>isWithin(target,root)))fail('ERR_QUALITY_ENV_FONT_PATH');
 const info=await stat(target);
 if(info.isDirectory()){
  if(state.directories.has(target))return;
  state.directories.add(target);
  const entries=await readdir(target,{withFileTypes:true});
  state.entries+=entries.length;if(state.entries>MAX_FONT_TREE_ENTRIES)fail('ERR_QUALITY_ENV_FONT_TREE_LIMIT');
  for(const entry of entries)await scanFontDirectory(join(target,entry.name),allowedRoots,state,depth+1);
 }else if(!info.isFile())fail('ERR_QUALITY_ENV_FONT_PATH');
}

async function validateFontTrees(paths,allowedRoots=FONT_ROOTS,state={directories:new Set(),entries:0}){
 for(const path of paths)await scanFontDirectory(path,allowedRoots,state,0);
}

export async function validateFontTree(path,allowedRoots=FONT_ROOTS){
 if(typeof path!=='string'||!isAbsolute(path)||!Array.isArray(allowedRoots)||!allowedRoots.length||allowedRoots.some(root=>typeof root!=='string'||!isAbsolute(root)))fail('ERR_QUALITY_ENV_FONT_PATH');
 await validateFontTrees([path],allowedRoots);
}

async function collectConfigPath(path,optional,state,depth=0){
 if(depth>MAX_CONFIG_DEPTH||state.paths.size>=MAX_CONFIGS)fail('ERR_QUALITY_ENV_CONFIG_LIMIT');
 let target;
 try{target=await realpath(path);}
 catch(error){if(error?.code==='ENOENT'&&optional)return;if(error?.code==='ENOENT')fail('ERR_QUALITY_ENV_CONFIG');throw error;}
 if(!CONFIG_ROOTS.some(root=>isWithin(target,root)))fail('ERR_QUALITY_ENV_CONFIG');
 const info=await stat(target);
 if(info.isDirectory()){
  if(state.directories.has(target))return;
  state.directories.add(target);
  const entries=await readdir(path);
  if(entries.length>MAX_PATHS)fail('ERR_QUALITY_ENV_CONFIG_LIMIT');
  for(const name of entries.filter(name=>/^[0-9].*\.conf$/.test(name)).sort())await collectConfigPath(join(path,name),false,state,depth+1);
  return;
 }
 if(!info.isFile())fail('ERR_QUALITY_ENV_CONFIG');
 const key=resolve(path);if(state.paths.has(key))return;
 state.paths.add(key);
 if(state.paths.size>MAX_CONFIGS)fail('ERR_QUALITY_ENV_CONFIG_LIMIT');
 const text=await stableConfigText(path,target);
 state.totalBytes+=Buffer.byteLength(text);if(state.totalBytes>MAX_CONFIG_TOTAL_BYTES)fail('ERR_QUALITY_ENV_CONFIG_LIMIT');
 for(const reference of validateConfigReferences(key,text)){
  if(reference.kind==='user-config'||reference.kind==='user-font-dir')continue;
  if(reference.kind==='dir')state.fontDirs.add(reference.path);
  else await collectConfigPath(reference.path,reference.optional,state,depth+1);
 }
}

async function collectFonts(){
 const {stdout}=await run('/usr/bin/fc-list',['-f','%{file}\n']);
 const paths=new Set();
 for(const raw of stdout.split(/\r?\n/)){
  const path=raw.trim();if(!path)continue;
  if(!isAbsolute(path))fail('ERR_QUALITY_ENV_FONT_PATH');
  let target;
  try{target=await realpath(path);}catch{fail('ERR_QUALITY_ENV_FONT_PATH');}
  if(!FONT_ROOTS.some(root=>isWithin(target,root)))fail('ERR_QUALITY_ENV_FONT_PATH');
  const info=await stat(target);if(!info.isFile())fail('ERR_QUALITY_ENV_FONT_PATH');
  paths.add(path);
 }
 return fingerprintFiles([...paths]);
}

async function collectConfigs(){
 const state={paths:new Set(),directories:new Set(),fontDirs:new Set(),totalBytes:0};
 await collectConfigPath('/etc/fonts/fonts.conf',false,state);
 await collectConfigPath('/usr/share/fontconfig/fonts.conf',true,state);
 await validateFontTrees([...state.fontDirs]);
 return fingerprintFiles([...state.paths].sort());
}

export async function collectEnvironment(){
 await rejectUserFontConfiguration();
 const configs=await collectConfigs();
 const {stdout}=await run('/usr/bin/google-chrome',['--version']);
 const chromeVersion=stdout.trim();if(!chromeVersion)fail('ERR_QUALITY_ENV_CHROME');
 const fonts=await collectFonts();
 return {chromeVersion,fonts,configs};
}

async function verifyAdjacentManifest(){
 const ownFile=fileURLToPath(import.meta.url);
 const manifestPath=join(dirname(ownFile),'quality-environment.json');
 const {readFile}=await import('node:fs/promises');
 let expected;
 try{expected=JSON.parse(await readFile(manifestPath,'utf8'));}
 catch{fail('ERR_QUALITY_ENV_MANIFEST');}
 compareEnvironment(expected,await collectEnvironment());
 process.stdout.write('Quality browser and font environment matches the pinned manifest.\n');
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))await verifyAdjacentManifest();
