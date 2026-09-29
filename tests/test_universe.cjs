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
    const listeners=new Map();
    return {innerHTML:'',textContent:'',value:'',checked:false,style:{},children:[],
      classList:{toggle(){},remove(){}},querySelectorAll(){return [];},
      addEventListener(type,callback){
        if(!listeners.has(type))listeners.set(type,[]);
        listeners.get(type).push(callback);
      },
      dispatchEvent(event){
        for(const callback of listeners.get(event.type)||[])callback({...event,target:event.target||this});
      },
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
  assert.equal(result.evaluate('[...allRows[0]._exchanges].sort().join(",")'),'mexc,okx');
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

test('futures availability survives filtering and futures exchange count sorting',async()=>{
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

test('Bitfinex is listed at zero volume and links use native asset codes',async()=>{
  const data={exchanges:['bitfinex','bybit'],pairs:[
    spot('BTC','USDT',[{...venue('bitfinex'),symbol:'tBTCUST'},venue('bybit',10)]),
    spot('DASH','USDT',[{...venue('bitfinex'),symbol:'tDSHUST'}]),
    spot('LONG','USDC',[{...venue('bitfinex'),symbol:'tLONG:UDC'}]),
    spot('BTC','USD',[venue('bybit')]),
  ]};
  const result=await page(data,{
    'fut_universe_bitfinex.json':futures('bitfinex',[
      contract('BTC','USDT'),contract('DASH','USDT'),contract('BTC','USD',{is_active:false}),
    ]),
    'fut_universe_bybit.json':futures('bybit',[]),
  });
  assert.deepEqual(result.requests,['spot_universe_combined.json','fut_universe_bitfinex.json','fut_universe_bybit.json']);
  assert.match(result.evaluate('allRows[0]._distHTML'),/>Bitfinex<\/a>/);
  assert.equal(result.evaluate('allRows[0]._futuresHTML'),'Bitfinex');
  assert.equal(result.evaluate('allRows[3]._futuresHTML'),'—');
  for(const [index,native] of [[0,'BTC%3AUST'],[1,'DSH%3AUST'],[2,'LONG%3AUDC']]){
    const link='https://trading.bitfinex.com/t/'+native+'?type=exchange';
    assert.ok(result.evaluate(`allRows[${index}]._distHTML`).includes(link));
  }
  assert.match(result.nodes.get('includeExchanges').innerHTML,/Bitfinex/);
  assert.match(result.nodes.get('excludeExchanges').innerHTML,/Bitfinex/);
});

function exchangeFilterPage(){
  return page({exchanges:['binance','bitget','bybit'],pairs:[
    spot('BTC','USDT',[venue('bitget'),venue('binance',10),venue('bybit',20)]),
    spot('ETH','USDT',[venue('bitget',20),venue('bybit',30)]),
    spot('SOL','USDT',[venue('bitget')]),
    spot('BNB','USDT',[venue('binance',8)]),
    spot('XRP','USDT',[venue('bybit',15)]),
    spot('BTC','USD',[venue('bitget',40)]),
    spot('ETH','USD',[venue('binance',10)]),
  ]},{
    // A Binance futures listing must not affect spot inclusion or exclusion.
    'fut_universe_binance.json':futures('binance',[contract('SOL','USDT')]),
    'fut_universe_bitget.json':futures('bitget',[]),
    'fut_universe_bybit.json':futures('bybit',[]),
  });
}

function selectExchange(result,group,exchange,checked=true){
  result.nodes.get(group+'Exchanges').dispatchEvent({
    type:'change',target:{type:'checkbox',value:exchange,checked},
  });
}

function matchMode(result,mode){
  result.nodes.get(mode==='all'?'exchangeMatchAll':'exchangeMatchAny').dispatchEvent({
    type:'change',target:{value:mode,checked:true},
  });
}

function shownPairs(result){
  return Array.from(result.evaluate('filtered.map(row=>row.pair)'));
}

test('any inclusion keeps pairs on at least one selection, including other venues and zero volume',async()=>{
  const result=await exchangeFilterPage();
  assert.equal(shownPairs(result).length,7);
  selectExchange(result,'include','bitget');
  assert.deepEqual(shownPairs(result),['BTC/USDT','ETH/USDT','SOL/USDT','BTC/USD']);
  selectExchange(result,'include','bybit');
  assert.deepEqual(shownPairs(result),['BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BTC/USD']);
  assert.equal(result.nodes.get('exchangeFilterSummary').textContent,'Listed on any of: Bitget, Bybit · No exclusions');
  selectExchange(result,'include','bitget',false);
  assert.deepEqual(shownPairs(result),['BTC/USDT','ETH/USDT','XRP/USDT']);
});

test('all inclusion requires every selected exchange and allows additional exchanges',async()=>{
  const result=await exchangeFilterPage();
  selectExchange(result,'include','bitget');
  selectExchange(result,'include','bybit');
  matchMode(result,'all');
  assert.deepEqual(shownPairs(result),['BTC/USDT','ETH/USDT']);
  assert.equal(result.nodes.get('exchangeFilterSummary').textContent,'Listed on all of: Bitget, Bybit · No exclusions');
  selectExchange(result,'include','binance');
  assert.deepEqual(shownPairs(result),['BTC/USDT']);
  matchMode(result,'any');
  assert.equal(shownPairs(result).length,7);
});

test('Bitget inclusion and Binance exclusion combine in either inclusion mode',async()=>{
  const result=await exchangeFilterPage();
  selectExchange(result,'include','bitget');
  selectExchange(result,'exclude','binance');
  assert.deepEqual(shownPairs(result),['ETH/USDT','SOL/USDT','BTC/USD']);
  assert.match(result.nodes.get('exchangeFilterSummary').textContent,/Not listed on: Binance/);
  // SOL stays despite its Binance futures listing.
  assert.match(result.nodes.get('tbody').innerHTML,/class="exchange-list">Binance<\/td>/);
  selectExchange(result,'include','bybit');
  matchMode(result,'all');
  assert.deepEqual(shownPairs(result),['ETH/USDT']);
});

test('empty inclusion is unrestricted in both modes and multiple exclusions reject any match',async()=>{
  const result=await exchangeFilterPage();
  matchMode(result,'all');
  assert.equal(shownPairs(result).length,7);
  selectExchange(result,'exclude','binance');
  selectExchange(result,'exclude','bybit');
  assert.deepEqual(shownPairs(result),['SOL/USDT','BTC/USD']);
  matchMode(result,'any');
  assert.deepEqual(shownPairs(result),['SOL/USDT','BTC/USD']);
  selectExchange(result,'exclude','bitget');
  assert.deepEqual(shownPairs(result),[]);
  assert.match(result.nodes.get('tbody').innerHTML,/colspan="8".*No pairs match these filters/);
  result.nodes.get('resetExchangeFilters').dispatchEvent({type:'click'});
  assert.equal(shownPairs(result).length,7);
  assert.equal(result.nodes.get('exchangeFilterSummary').textContent,'All spot pairs · No exclusions');
});

test('moving an exchange between include and exclude clears the opposite selection',async()=>{
  const result=await exchangeFilterPage();
  selectExchange(result,'include','bitget');
  selectExchange(result,'exclude','bitget');
  assert.equal(result.evaluate('includedExchanges.size'),0);
  assert.deepEqual(shownPairs(result),['BNB/USDT','XRP/USDT','ETH/USD']);
  selectExchange(result,'include','bitget');
  assert.equal(result.evaluate('excludedExchanges.size'),0);
  assert.deepEqual(shownPairs(result),['BTC/USDT','ETH/USDT','SOL/USDT','BTC/USD']);
});

test('exchange filters compose with search, quote, primary exchange, and sorting; reset preserves other filters',async()=>{
  const result=await exchangeFilterPage();
  selectExchange(result,'include','bitget');
  selectExchange(result,'exclude','binance');
  result.nodes.get('quoteFilter').value='USDT';
  result.nodes.get('quoteFilter').dispatchEvent({type:'change'});
  assert.deepEqual(shownPairs(result),['ETH/USDT','SOL/USDT']);
  result.evaluate("sortCol='pair';sortAsc=false;doSort()");
  assert.deepEqual(shownPairs(result),['SOL/USDT','ETH/USDT']);
  result.nodes.get('search').value='eth';
  result.nodes.get('search').dispatchEvent({type:'input'});
  result.nodes.get('primaryExchangeFilter').value='bybit';
  result.nodes.get('primaryExchangeFilter').dispatchEvent({type:'change'});
  assert.deepEqual(shownPairs(result),['ETH/USDT']);
  matchMode(result,'all');
  result.nodes.get('resetExchangeFilters').dispatchEvent({type:'click'});
  assert.deepEqual(shownPairs(result),['ETH/USDT']);
  assert.equal(result.evaluate('includedExchanges.size+excludedExchanges.size'),0);
  assert.equal(result.nodes.get('exchangeMatchAny').checked,true);
  assert.equal(result.nodes.get('exchangeMatchAll').checked,false);
  assert.equal(result.nodes.get('search').value,'eth');
  assert.equal(result.nodes.get('quoteFilter').value,'USDT');
  assert.equal(result.nodes.get('primaryExchangeFilter').value,'bybit');
});

test('table omits the spot exchanges column while retaining futures and spot trading links',async()=>{
  const result=await exchangeFilterPage();
  assert.doesNotMatch(html,/<th[^>]*>Spot exchanges<\/th>/);
  assert.equal((html.match(/<th data-col=/g)||[]).length,8);
  const firstRow=result.nodes.get('tbody').innerHTML.match(/<tr>(.*?)<\/tr>/)[1];
  assert.equal((firstRow.match(/<td[ >]/g)||[]).length,8);
  assert.match(html,/<th data-col="futures">Futures exchanges<\/th>/);
  assert.match(firstRow,/www.bitget.com\/spot\/BTCUSDT/);
});
