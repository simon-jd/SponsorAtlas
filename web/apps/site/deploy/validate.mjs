// Offline safety/contract checks, deliberately not ARM validation or provisioning.
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const t=JSON.parse(readFileSync(new URL('./azure-resources.json',import.meta.url)));
for(const r of t.resources){assert.ok(!/registry|workspace|virtualNetwork/i.test(r.type));if(r.tags){assert.equal(r.tags.project,'sponsoratlas');assert.equal(r.tags.owner,'codex-sponsoratlas');assert.ok(r.tags.ttl);}}
const site=t.resources.find(r=>r.type==='Microsoft.Web/staticSites');assert.equal(site.sku.name,'Free');
const app=t.resources.find(r=>r.type==='Microsoft.App/containerApps');assert.equal(app.properties.workloadProfileName,'Consumption');assert.equal(app.properties.template.scale.minReplicas,0);assert.equal(app.properties.template.scale.maxReplicas,1);assert.equal(app.properties.configuration.ingress.allowInsecure,false);
const account=t.resources.find(r=>r.type==='Microsoft.Storage/storageAccounts');assert.equal(account.sku.name,'Standard_LRS');assert.equal(account.properties.allowBlobPublicAccess,false);
console.log('PASS: offline Free/Consumption/min0/max1/privateBlob constraints; no cloud call');
