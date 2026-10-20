from pathlib import Path
from playwright.sync_api import sync_playwright
source=(Path(__file__).resolve().parents[1] / 'server/gizmoapp_server/static/app/course-media.js').read_text()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page()
    result=page.evaluate('''async source => {
      const api = await import(URL.createObjectURL(new Blob([source], {type:'text/javascript'})));
      let now=0; Date.now=()=>now;
      window.setTimeout=(fn,ms)=> { if(ms===2000) {now+=ms; queueMicrotask(fn);} return 1; };
      window.clearTimeout=()=>{};
      let submits=0, polls=0, firstReadyAt=0, chunks=0;
      window.fetch=async (url, options)=> {
        if(options.credentials || JSON.stringify(options).includes('Bearer')) throw new Error('browser credential');
        if(url.endsWith('/speech')) {submits++; return new Response(JSON.stringify({pollTicket:'ticket',status:'queued'}), {status:202});}
        polls++;
        if(polls===1) throw new TypeError('temporary outage');
        if(polls===2) return new Response('{}',{status:503});
        if(polls<82) return new Response(JSON.stringify({pollTicket:'ticket',status:'running'}),{status:202});
        return new Response('MP3chunk',{headers:{'Content-Type':'audio/mpeg'}});
      };
      await api.narrate('/prefix/api', 'A sentence. '.repeat(80), {
        onChunk: ()=> {chunks++; if(!firstReadyAt) firstReadyAt=submits;}
      });
      if(now<160000 || submits!==2 || chunks!==2 || firstReadyAt!==1) throw new Error(JSON.stringify({now,submits,chunks,firstReadyAt}));
      window.fetch=async()=>new Response(JSON.stringify({errors:['denied']}),{status:403});
      let denied=false; try {await api.generateMedia('/prefix/api','image',{prompt:'x'});} catch(e) {denied=e.message==='denied';}
      if(!denied) throw new Error('terminal failure not surfaced');
      let count=0; window.fetch=async()=> {count++; throw new TypeError('lost submission');};
      try {await api.generateMedia('/prefix/api','image',{prompt:'x'});} catch(e) {}
      if(count!==1) throw new Error('duplicate submission');
      return {simulatedColdSeconds:now/1000,submits,chunks,firstReadyAt,terminalErrors:true,noDuplicateSubmit:true};
    }''',source)
    print(result)
    browser.close()
