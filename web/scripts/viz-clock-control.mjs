// Diagnostic only: an empty WebGL canvas cannot satisfy the 544-cell workload gate.
import {chromium} from '../apps/site/node_modules/@playwright/test/index.mjs';
import {writeFileSync} from 'node:fs';
const flags=['--no-sandbox','--enable-webgl','--ignore-gpu-blocklist','--use-angle=vulkan','--enable-features=Vulkan','--disable-vulkan-surface'];
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',args:flags});
try{
 const page=await browser.newPage({viewport:{width:1280,height:900}});await page.setContent('<canvas width="1168" height="420"></canvas>');
 const result=await page.evaluate(async()=>{const gl=document.querySelector('canvas').getContext('webgl2');if(!gl)throw Error('No WebGL2');const debug=gl.getExtension('WEBGL_debug_renderer_info');gl.clearColor(.07,.18,.25,1);gl.clear(gl.COLOR_BUFFER_BIT);const timestamps=[],callbackNow=[],rafIntervals=[],nowIntervals=[];let previousRaf=0,previousNow=0;for(let i=0;i<300;i++)await new Promise(resolve=>requestAnimationFrame(t=>{const now=performance.now();if(i>=60){timestamps.push(t);callbackNow.push(now);rafIntervals.push(t-previousRaf);nowIntervals.push(now-previousNow);}previousRaf=t;previousNow=now;resolve();}));return {renderer:gl.getParameter(debug.UNMASKED_RENDERER_WEBGL),vendor:gl.getParameter(debug.UNMASKED_VENDOR_WEBGL),timestamps,callbackNow,rafIntervals,nowIntervals};});
 const p95=values=>[...values].sort((a,b)=>a-b)[Math.ceil(.95*values.length)-1];
 writeFileSync('evidence/viz-clock-control.json',JSON.stringify({...result,kind:'DIAGNOSTIC_EMPTY_WEBGL_NOT_ACCEPTANCE',cells:0,warmupFrames:60,measuredFrames:240,viewport:[1280,900],canvas:[1168,420],flags,rafP95:p95(result.rafIntervals),performanceNowP95:p95(result.nowIntervals),definition:'unrounded nearest rank ceil(.95*n)-1'},null,2));console.log(JSON.stringify({renderer:result.renderer,rafP95:p95(result.rafIntervals),performanceNowP95:p95(result.nowIntervals)}));
}finally{await browser.close();}
