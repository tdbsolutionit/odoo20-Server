// Integration probe for an isolated Odoo database, using installed Edge/CDP.
// Credentials and fixture IDs are supplied in the process environment only.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, readFile } from 'node:fs/promises';

const base = process.env.LIVE_TEST_URL;
assert(base && process.env.LIVE_TEST_PASSWORD && process.env.LIVE_TEST_DB);
let cookie = '';
async function rpc(path, params) {
    const response = await fetch(base + path, {method: 'POST', headers: {'Content-Type': 'application/json', Cookie: cookie}, body: JSON.stringify({jsonrpc:'2.0',method:'call',params,id:1})});
    for (const entry of response.headers.getSetCookie()) {
        if (entry.startsWith('session_id=')) cookie = entry.split(';')[0];
    }
    const result = await response.json();
    if (result.error) throw new Error(JSON.stringify(result.error));
    return result.result;
}
await rpc('/web/session/authenticate', {db:process.env.LIVE_TEST_DB,login:process.env.LIVE_TEST_LOGIN || 'admin',password:process.env.LIVE_TEST_PASSWORD});
const profile = process.env.LIVE_TEST_PROFILE + '-' + process.pid;
await mkdir(profile, {recursive:true});
const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
    '--headless=new','--disable-gpu','--no-sandbox','--disable-extensions','--no-first-run','--no-default-browser-check',
    '--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank',
], {windowsHide:true,stdio:['ignore','ignore','pipe']});
let browserLog='';
browser.stderr.on('data', chunk => { browserLog += chunk.toString(); });
const sleep = (ms) => new Promise(resolve => setTimeout(resolve,ms));
let ws;
const keepAlive = setInterval(() => {}, 1000);
try {
    let target;
    for (let i=0;i<80;i++) {
        try {
            const port=(await readFile(profile+'/DevToolsActivePort','utf8')).split('\n')[0];
            const tabs = await (await fetch('http://127.0.0.1:'+port+'/json')).json();
            target = tabs.find(tab => tab.type === 'page');
            if (target) break;
        } catch {}
        await sleep(250);
    }
    assert(target, 'Headless browser did not start');
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve,reject) => {ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
    let sequence=0;
    const pending = new Map();
    const exceptions=[];
    ws.addEventListener('close', event => {
        for (const handler of pending.values()) {clearTimeout(handler.timeout);handler.reject(new Error('Browser CDP closed: '+event.code+' '+event.reason+' '+browserLog.slice(-3000)));}
        pending.clear();
    });
    ws.addEventListener('message', event => {
        const message=JSON.parse(event.data);
        if (message.id) {
            const handler=pending.get(message.id);
            if (handler) {pending.delete(message.id);clearTimeout(handler.timeout);message.error?handler.reject(new Error(JSON.stringify(message.error))):handler.resolve(message.result);}
        } else if (message.method==='Runtime.exceptionThrown') {
            exceptions.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
        }
    });
    function call(method,params={}) {
        return new Promise((resolve,reject) => {const id=++sequence;const timeout=setTimeout(() => {pending.delete(id);reject(new Error('CDP timeout: '+method));},120000);pending.set(id,{resolve,reject,timeout});ws.send(JSON.stringify({id,method,params}));});
    }
    async function evaluate(expression) {
        const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
        if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
        return result.result.value;
    }
    async function waitFor(expression,timeout=60000) {
        const end=Date.now()+timeout;
        while(Date.now()<end) {
            if(await evaluate(expression)) return;
            if(exceptions.length) throw new Error(exceptions.join('\n'));
            await sleep(200);
        }
        const details = await evaluate(`JSON.stringify({
            buttons: [...document.querySelectorAll('button, a[name]')].map(el => [el.tagName, el.getAttribute('name'), el.textContent.trim().slice(0, 100)]),
            text: document.body.innerText.slice(0, 3000),
        })`);
        throw new Error('Browser wait timed out: '+expression+'\n'+details);
    }
    await call('Runtime.enable');
    await call('Network.enable');
    const [name,value]=cookie.split('=');
    await call('Network.setCookie',{name,value,url:base,httpOnly:true});
    await call('Page.navigate',{url:base+'/odoo'});
    await waitFor('Boolean(window.odoo?.__WOWL_DEBUG__?.root?.env?.services?.action)',120000);
    console.log('Browser webclient loaded');
    if (process.env.LIVE_TEST_MODE === 'workflow') {
        const action = await rpc('/web/dataset/call_kw/mail.activity/action_tdb_open_workflow', {
            model: 'mail.activity', method: 'action_tdb_open_workflow',
            args: [[Number(process.env.LIVE_TEST_TASK_ID)]], kwargs: {},
        });
        await evaluate(`odoo.__WOWL_DEBUG__.root.env.services.action.doAction(${Number(process.env.LIVE_TEST_ACTIVITY_ACTION_ID)})`);
        await waitFor("[...document.querySelectorAll('.o_data_row')].some(row => row.textContent.includes('Workflow browser Part'))", 30000);
        await evaluate(`(() => {
            const row = [...document.querySelectorAll('.o_data_row')].find(row => row.textContent.includes('Workflow browser Part'));
            row.querySelector('td[name="res_name"]').click();
        })()`);
        await waitFor("Boolean(document.querySelector('.o_form_view .o-mail-Chatter'))", 30000);
        await waitFor("document.querySelector('.o_form_view .o-mail-Chatter')?.textContent.includes('PLM: Review requested')", 30000);
        await waitFor("document.querySelector('.o_form_view .o-mail-Chatter')?.textContent.includes('Submitted for review')", 30000);
        await waitFor("document.querySelector('.o-mail-Thread-content')?.textContent.includes('Part completion feedback')", 30000);
        const timeline = await evaluate("document.querySelector('.o-mail-Thread-content').textContent");
        assert(timeline.indexOf('Timeline after Part completion') < timeline.indexOf('Part completion feedback'));
        assert(timeline.indexOf('Part completion feedback') < timeline.indexOf('Timeline before Part completion'));
        assert(!timeline.includes('Recently Completed Part Activities'));
        const doneButtons = await evaluate(`(() => {
            const activities = [...document.querySelectorAll('.o-mail-Chatter .o-mail-Activity')];
            const ecr = activities.find((row) => row.textContent.includes('Browser ECR form-only completion'));
            const todo = activities.find((row) => row.textContent.includes('Browser ordinary todo'));
            return { ecr: ecr?.querySelectorAll('.o-mail-Activity-markDone').length,
                todo: todo?.querySelectorAll('.o-mail-Activity-markDone').length };
        })()`);
        assert.deepEqual(doneButtons, { ecr: 0, todo: 1 });
        const badge = "Number(document.querySelector('.o-mail-ActivityMenu-counter')?.textContent || 0)";
        await waitFor(badge + " > 0", 10000);
        const badgeBefore = await evaluate(badge);
        await evaluate(`(() => {
            window.tdbBadgeEvents = 0;
            odoo.__WOWL_DEBUG__.root.env.services.bus_service.subscribe(
                'tdb_plm/activity_counter_changed', () => window.tdbBadgeEvents++);
        })()`);
        for (let index = 0; index < 2; index++) {
            await rpc('/web/dataset/call_kw/product.product/activity_schedule', {
                model: 'product.product', method: 'activity_schedule',
                args: [[action.res_id], 'mail.mail_activity_data_todo'],
                kwargs: {date_deadline: '2099-12-31', summary: 'Future clock activity ' + index},
            });
        }
        await waitFor("window.tdbBadgeEvents > 0", 10000);
        await waitFor(badge + " === " + (badgeBefore + 2), 12000);
        console.log('Clock counted both future tasks on the same Part without a page refresh');
        assert.equal(exceptions.length, 0, exceptions.join('\n'));
        console.log('Activity row opened the Part form with its review activity and decision history');
        // Odoo may place secondary header buttons in its overflow menu.
        const rejection = await rpc('/web/dataset/call_button', {
            model: action.res_model, method: 'action_tdb_reject', args: [[action.res_id]], kwargs: {},
        });
        await evaluate(`odoo.__WOWL_DEBUG__.root.env.services.action.doAction(${JSON.stringify(rejection)})`);
        await waitFor("Boolean(document.querySelector('.modal .o_field_widget[name=\"reason\"]'))", 30000);
        const layout = await evaluate(`(() => {
            const modal = document.querySelector('.modal');
            const banner = modal.querySelector('.alert-info');
            return {
                bannerWidth: banner.getBoundingClientRect().width,
                bodyWidth: modal.querySelector('.modal-body').getBoundingClientRect().width,
                obsoleteFields: modal.querySelectorAll('[name="failed_part_ids"], [name="failed_document_ids"], [name="rejection_scope"]').length,
            };
        })()`);
        assert.equal(layout.obsoleteFields, 0);
        assert(layout.bannerWidth > layout.bodyWidth * 0.75, JSON.stringify(layout));
        assert.equal(exceptions.length, 0, exceptions.join('\n'));
        console.log('Recursive rejection dialog rendered without selectors; banner spans the form');
    } else {
    await evaluate(`odoo.__WOWL_DEBUG__.root.env.services.action.doAction({type:'ir.actions.act_window',name:'PLM Live Test',res_model:'ir.attachment',res_id:${Number(process.env.LIVE_TEST_DOC_ID)},views:[[${Number(process.env.LIVE_TEST_FORM_ID)},'form']],target:'current',context:{odooPLM:true}})`);
    await waitFor("Boolean(document.querySelector('.o_form_view'))");
    await evaluate('document.activeElement?.blur()');
    const marker='LIVE-SERVER-'+Date.now();
    const started=Date.now();
    await rpc('/web/dataset/call_kw/ir.attachment/write',{model:'ir.attachment',method:'write',args:[[Number(process.env.LIVE_TEST_DOC_ID)],{engineering_code:marker}],kwargs:{context:{odooPLM:true}}});
    await waitFor(`document.querySelector('.o_form_view')?.textContent.includes(${JSON.stringify(marker)})`,12000);
    console.log('Server change reached open form in',Date.now()-started,'ms');
    const inputExists=await evaluate("Boolean(document.querySelector('.o_form_view .o_field_widget[name=\"description\"] input, .o_form_view .o_field_widget[name=\"description\"] textarea, .o_form_view .o_field_widget[name=\"name\"] input'))");
    assert(inputExists,'No editable fixture field found');
    await evaluate(`(() => { const input=document.querySelector('.o_form_view .o_field_widget[name="description"] input, .o_form_view .o_field_widget[name="description"] textarea, .o_form_view .o_field_widget[name="name"] input');window.liveProbeInput=input;input.focus();input.value='UNSAVED-LIVE-EDIT';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await rpc('/web/dataset/call_kw/ir.attachment/write',{model:'ir.attachment',method:'write',args:[[Number(process.env.LIVE_TEST_DOC_ID)],{engineering_code:marker+'-SECOND'}],kwargs:{context:{odooPLM:true}}});
    await sleep(2500);
    assert.equal(await evaluate("window.liveProbeInput?.isConnected && window.liveProbeInput.value === 'UNSAVED-LIVE-EDIT'"),true);
    assert.equal(exceptions.length,0,exceptions.join('\n'));
    console.log('Unsaved browser edit preserved during server change');
    }
} finally {
    if(ws?.readyState===WebSocket.OPEN) {
        ws.send(JSON.stringify({id:999999,method:'Browser.close'}));
        ws.close();
    }
    browser.kill();
    clearInterval(keepAlive);
}
