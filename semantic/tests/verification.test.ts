import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
const require=createRequire(import.meta.url);
const {assertNumericEqual,assertMetric,stopChild}=require('../verification.cjs');
test('acceptance rejects missing, nonnumeric and null-as-zero values',()=>{
 for(const value of [undefined,'invalid',null,NaN,Infinity,-Infinity,'','  ',false,[],{}])assert.throws(()=>assertNumericEqual(value,0));
 for(const value of [undefined,'invalid',NaN,Infinity,''])assert.throws(()=>assertNumericEqual(0,value));
 assert.throws(()=>assertMetric({},'count',0));assert.throws(()=>assertMetric(Object.create({count:0}),'count',0));
 assert.throws(()=>assertMetric({count:null},'count',0));assert.throws(()=>assertMetric({count:'invalid'},'count',42));
});
test('verification waits for graceful child cleanup and rejects forced/nonzero exits',async()=>{
 const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>setTimeout(()=>process.exit(0),50));setInterval(()=>{},1000);console.log('ready');"],{stdio:['ignore','pipe','ignore']});
 await new Promise(resolve=>child.stdout!.once('data',resolve));let finished=false;
 const stopping=stopChild(child).then(()=>{finished=true;});await new Promise(resolve=>setTimeout(resolve,10));assert.equal(finished,false);await stopping;assert.equal(child.exitCode,0);
 const stuck=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000);console.log('ready');"],{stdio:['ignore','pipe','ignore']});
 await new Promise(resolve=>stuck.stdout!.once('data',resolve));await assert.rejects(stopChild(stuck,25),/cleanup/);assert.equal(stuck.signalCode,'SIGKILL');
 const failed=spawn(process.execPath,['-e','process.exit(1)'],{stdio:'ignore'});await new Promise(resolve=>failed.once('exit',resolve));await assert.rejects(stopChild(failed),/cleanup/);
});
test('acceptance supports finite numbers/numeric strings, strict null and tolerance',()=>{
 for(const [a,b] of [[0,'0'],['42',42],['1.25','1.25'],['1e2',100],[-5,'-5'],[null,null]])assert.doesNotThrow(()=>assertNumericEqual(a,b));
 assert.doesNotThrow(()=>assertNumericEqual(42.0000001,42));assert.throws(()=>assertNumericEqual(43,42));assert.throws(()=>assertNumericEqual(0,null));assert.throws(()=>assertNumericEqual(undefined,null));assert.doesNotThrow(()=>assertMetric({count:null},'count',null));
});
test('secondary-role state accepts the live Snowflake format and rejects any active role',()=>{
 const {secondaryRolesDisabled}=require('../source.cjs');
 // 2026-10-31, CUBE_SVC with DEFAULT_SECONDARY_ROLES=() after USE SECONDARY ROLES NONE.
 assert.equal(secondaryRolesDisabled('{"roles":"","value":""}'),true);
 assert.equal(secondaryRolesDisabled('{"roles":"","value":"NONE"}'),true);
 for(const text of ['{"roles":"PII_READER","value":""}','{"roles":"","value":"ALL"}','{"roles":["ANALYST"],"value":"NONE"}','{"value":""}','{}','[]','not json',null,undefined])assert.equal(secondaryRolesDisabled(text),false,String(text));
});
test('exact metric equality has no tolerance',()=>{
 const {assertExactEqual}=require('../verification.cjs');
 assert.doesNotThrow(()=>assertExactEqual(697613/713467,697613/713467));assert.doesNotThrow(()=>assertExactEqual('713467',713467));
 assert.throws(()=>assertExactEqual(0.977779,697613/713467));assert.throws(()=>assertExactEqual(null,1));assert.throws(()=>assertExactEqual('x',1));
});
test('a rate equals numerator / denominator exactly: as a double, or as the decimal Snowflake rounds to',()=>{
 const {assertExactRatio}=require('../verification.cjs');
 // 2026-10-31: Snowflake returned 0.977779 for 1.0 * 697613 / 713467 (NUMBER scale 6, rounded).
 for(const value of ['0.977779',697613/713467,String(697613/713467),'0.9777789302','0.97777893021'])assert.doesNotThrow(()=>assertExactRatio(value,697613,713467),String(value));
 for(const value of ['0.977778','0.97778','0.9777790',0.977779,null,'x'])assert.throws(()=>assertExactRatio(value,697613,713467),undefined,String(value));
 assert.doesNotThrow(()=>assertExactRatio('0.5',3,6));assert.doesNotThrow(()=>assertExactRatio('0.500000',3,6));assert.doesNotThrow(()=>assertExactRatio(null,0,0));assert.throws(()=>assertExactRatio('0',0,0));
 assert.doesNotThrow(()=>assertExactRatio('0.666667',2,3));assert.throws(()=>assertExactRatio('0.666666',2,3));
});
