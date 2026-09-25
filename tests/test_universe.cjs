const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const html=fs.readFileSync(path.join(__dirname,'../universe.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];

function venue(exchange,volume=0){
  return {exchange,quote_volume:volume,quote_currency:'USDT',volume_pct:0};
}
function spot(base,quote,venues){
  return {pair:base+'/'+quote,base_asset:base,quote_asset:quote,venues,
    venue_count:venues.length,total_quote_volume:0,total_usdt_volume:0};
}
function contract(base,quote,flags={is_active:true}){
  return {base_asset:base,quote_asset:quote,pair:base+'/'+quote,flags};
}
function futures(exchange,pairs,collection_status='ok'){
  return {exchange,pairs,collection_status,universe_type:'futures',generated_at:'2026-09-25T12:00:00Z'};
}

async function page(data,responses={}){
  const nodes=new Map();
  const requests=[];
  function element(){
    return {innerHTML:'',textContent:'',value:'',style:{},children:[],
      classList:{toggle(){},remove(){}},addEventListener(){},querySelectorAll(){return [];},
      appendChild(child){this.children.push(child);},insertAdjacentHTML(where,text){this.innerHTML+=text;}};
  }
  const document={
    getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},
    querySelectorAll(){return [];},addEventListener(){},createElement:element,
  };
  const context=vm.createContext({document,URL,AbortController,setTimeout,clearTimeout,
    window:{location:{href:'https://example.test/Crypto_universe/universe.html'}},
    requestAnimationFrame(callback){callback();},
    async fetch(url){
      const file=new URL(url).pathname.split('/').pop();
      requests.push(file);
      if(file==='spot_universe_combined.json')return {ok:true,json:async()=>data};
      const payload=responses[file];
      if(payload instanceof Error)throw payload;
      return {ok:payload!==undefined,status:payload===undefined?404:200,json:async()=>payload};
    },
  });
  vm.runInContext(script,context);
  // Allow the initial fetch, JSON parsing, and parallel futures requests to settle.
  await new Promise(resolve=>setImmediate(resolve));
  return {context,nodes,requests,evaluate:code=>vm.runInContext(code,context)};
}

test('shows active venues even at zero volume; exact quotes and multipliers stay separate',async()=>{
  const data={generated_at:'today',exchanges:['mexc','okx'],pairs:[
    spot('BTC','USDT',[venue('mexc'),venue('okx')]),
    spot('BTC','USD',[venue('okx')]),
    spot('PEPE','USDT',[venue('mexc')]),
  ]};
  const result=await page(data,{
    'fut_universe_mexc.json':futures('mexc',[
      contract('BTC','USDT'),contract('BTC','USDT'),contract('PEPE','USDT',{is_active:false}),
      contract('1000PEPE','USDT'),contract('BTC','USD',{is_tradable:'true'}),
    ]),
    'fut_universe_okx.json':futures('okx',[
      contract('BTC','USD'),contract('BTC','USDT',{is_active:false,is_tradable:true}),
      contract('PEPE','USDT',{}),
    ]),
  });
  assert.equal(result.evaluate('allRows[0]._spotExchanges.join(",")'),'mexc,okx');
  assert.equal(result.evaluate('allRows[0]._futuresExchanges.join(",")'),'mexc');
  assert.equal(result.evaluate('allRows[1]._futuresExchanges.join(",")'),'okx');
  assert.equal(result.evaluate('allRows[2]._futuresExchanges.length'),0);
  assert.match(result.nodes.get('tbody').innerHTML,/class="exchange-list">MEXC<\/td>/);
  assert.match(result.nodes.get('tbody').innerHTML,/mexc.com\/exchange\/BTC_USDT/);
  assert.equal(result.nodes.get('tableWrap').style.display,'block');
});

test('missing and failed snapshots display unknown availability without breaking spot data',async()=>{
  const data={exchanges:['mexc','bybit','okx'],pairs:[spot('BTC','USDT',[venue('mexc')])]};
  const result=await page(data,{
    'fut_universe_mexc.json':futures('mexc',[contract('BTC','USDT')]),
    'fut_universe_okx.json':futures('okx',[],'error'),
  });
  assert.match(result.nodes.get('futuresCoverage').textContent,/Incomplete: Bybit, OKX/);
  assert.match(result.evaluate('allRows[0]._futuresHTML'),/MEXC.*\+ \?/);
  assert.equal(result.evaluate('allRows.length'),1);
  assert.ok(result.requests.includes('fut_universe_bybit.json'));
});

test('partial snapshots contribute known contracts and unsupported exchanges do not create a gap',async()=>{
  const data={exchanges:['binance','upbit'],pairs:[spot('BTC','USDT',[venue('binance')])]};
  const partial=await page(data,{
    'fut_universe_binance.json':futures('binance',[contract('BTC','USDT')],'partial'),
    'fut_universe_upbit.json':futures('upbit',[],'unsupported'),
  });
  assert.equal(partial.evaluate('allRows[0]._futuresExchanges.join(",")'),'binance');
  assert.match(partial.nodes.get('futuresCoverage').textContent,/Incomplete: Binance/);
  assert.match(partial.nodes.get('futuresCoverage').textContent,/Futures unavailable: Upbit/);
  const unsupported=await page({...data,exchanges:['upbit']},{
    'fut_universe_upbit.json':futures('upbit',[],'unsupported'),
  });
  assert.equal(unsupported.evaluate('futuresIncomplete'),false);
  assert.equal(unsupported.evaluate('allRows[0]._futuresHTML'),'—');
});

test('mismatched payloads are rejected and only in-scope files are requested',async()=>{
  const data={exchanges:['bybit'],pairs:[spot('BTC','USDT',[venue('bybit')])]};
  const result=await page(data,{'fut_universe_bybit.json':futures('mexc',[contract('BTC','USDT')])});
  assert.equal(result.evaluate('allRows[0]._futuresExchanges.length'),0);
  assert.match(result.evaluate('allRows[0]._futuresHTML'),/Unknown/);
  assert.deepEqual(result.requests,['spot_universe_combined.json','fut_universe_bybit.json']);
});

test('new columns survive filtering and futures exchange count sorting',async()=>{
  const data={exchanges:['mexc','okx'],pairs:[
    spot('BTC','USDT',[venue('mexc',10),venue('okx')]),spot('ETH','USDT',[venue('okx',20)]),
  ]};
  const result=await page(data,{
    'fut_universe_mexc.json':futures('mexc',[contract('ETH','USDT',{is_tradable:true})]),
    'fut_universe_okx.json':futures('okx',[contract('ETH','USDT'),contract('BTC','USDT')]),
  });
  result.evaluate("sortCol='futures';sortAsc=false;doSort()");
  assert.equal(result.evaluate('filtered[0].pair'),'ETH/USDT');
  result.nodes.get('search').value='BTC';
  result.evaluate('applyFilters()');
  assert.equal(result.evaluate('filtered.length'),1);
  assert.equal(result.evaluate('filtered[0].pair'),'BTC/USDT');
  assert.match(result.nodes.get('tbody').innerHTML,/class="exchange-list">OKX<\/td>/);
});

test('malformed futures contracts cannot break the spot table',async()=>{
  const result=await page({exchanges:['okx'],pairs:[spot('BTC','USDT',[venue('okx')])]}, {
    'fut_universe_okx.json':futures('okx',[null]),
  });
  assert.equal(result.nodes.get('tableWrap').style.display,'block');
  assert.match(result.nodes.get('futuresCoverage').textContent,/Incomplete: OKX/);
});
