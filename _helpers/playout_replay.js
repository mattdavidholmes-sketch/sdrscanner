// Replays real arrival times (diag/probe_<MHz>_<mode>.json, made by _helpers/stream_probe.py) through the page's own playout
// routine, cut out of dashboard2.py as it stands, with the device's sound clock true, 0.2% fast, 0.2% slow and 1% fast.
// Says how many holes the player would have left, whether they fell in sound or in silence, and how often it changed speed.
//   node playout_replay.js <dashboard2.py> <probe.json> [fm|am]
const fs=require('fs');
const py=fs.readFileSync(process.argv[2],'utf8');const i=py.indexOf('var PO={nextT:0');const tail='PO.rep=now;PO.g0=PO.gaps;PO.low=9;}}';const j=py.indexOf(tail,i)+tail.length;
if(i<0||j<tail.length)throw new Error('playout routine not found in '+process.argv[2]);
const src=py.slice(i,j).replace(/\\\\/g,'\\');
function run(probe,kind,clock){const g={};const reports=[];const fetch=u=>{reports.push(u);return{catch(){}}};
 (new Function('g','fetch',src+';g.PO=PO;g.playout=playout;'))(g,fetch);const PO=g.PO;const ctx={currentTime:0};const arr=probe.arrivals;const t0=arr[0][0];
 let ch=0,last=1,mn=9,mx=0,low=9,hi=0,inSound=0,inSil=0,carry=0;
 for(const [t,n,rms] of arr){ctx.currentTime=(t-t0)*clock;let tot=carry+n;const smp=Math.floor(tot/2);if(smp<1200){carry=tot;continue;}carry=tot-smp*2;const d=smp/24000;
  const ab={duration:d,getChannelData:()=>new Float32Array(smp)};const s={playbackRate:{value:1},start(){}};const g0=PO.gaps;const lead=PO.nextT-ctx.currentTime;g.playout(ctx,ab,s,kind);
  if(PO.gaps>g0){if(rms>30)inSound++;else inSil++;}
  const r=s.playbackRate.value;if(r!==last){ch++;last=r;}mn=Math.min(mn,r);mx=Math.max(mx,r);if(t-t0>3){low=Math.min(low,lead);hi=Math.max(hi,lead);}}
 return 'holes '+PO.gaps+' ('+inSound+' in sound, '+inSil+' in silence), speed changes '+ch+' ('+mn.toFixed(3)+'-'+mx.toFixed(3)+'), in hand '+low.toFixed(2)+'-'+hi.toFixed(2)+' s, reports '+reports.length;}
const p=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));const kind=process.argv[4]||p.mode||'fm';
console.log(p.mhz+' '+p.mode+' '+p.when+': '+p.audio_s+' s of audio in '+p.span_s+' s, longest wait '+p.gap_max_s+' s, sound in '+Math.round(100*p.sound_fraction)+'% of it');
for(const [cn,c] of [['sound clock true',1],['0.2% fast',1.002],['0.2% slow',0.998],['1% fast',1.01]])console.log('  '+cn.padEnd(17)+run(p,kind,c));
