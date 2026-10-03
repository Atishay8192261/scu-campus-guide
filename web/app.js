'use strict';
const $ = id => document.getElementById(id);
let pc, stream, channel, ping, timer, callIdentity, closeToken, active = false;
let epoch = 0;
let choices = {};
let iceServers = [];
const selection = () => Object.fromEntries(Object.keys(choices).map(role => [role, $(role).value]));
function state(title, hint = '') { $('status').textContent = title; if (hint) $('hint').textContent = hint; }
async function request(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail && typeof data.detail === 'string' ? data.detail : 'Request could not be completed.');
  return data;
}
function showAnswer(answer, question) {
  const article = document.createElement('article'); article.className = 'exchange';
  const q = document.createElement('p'); q.className = 'question'; q.textContent = question || 'Campus guide'; article.append(q);
  const text = document.createElement('p'); text.textContent = answer.speech; article.append(text);
  for (const source of answer.citations || []) {
    const div = document.createElement('div'); div.className = 'sources';
    const url = new URL(source.url); if (url.protocol !== 'https:') continue;
    const link = document.createElement('a'); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = source.title; div.append(link);
    const quote = document.createElement('span'); quote.className = 'quote'; quote.textContent = '“' + source.quote + '” · checked ' + new Date(source.fetched_at).toLocaleString(); div.append(quote); article.append(div);
  }
  for (const [label, useful] of [['Helpful',true], ['Needs improvement',false]]) {
    const button = document.createElement('button'); button.className = 'rating'; button.textContent = label;
    button.onclick = async () => { try { await request('/api/v1/feedback', {answer_id:answer.id,useful}); for (const b of article.querySelectorAll('button')) b.disabled = true; button.textContent = 'Feedback saved'; } catch (e) { state(e.message); } }; article.append(button);
  }
  $('conversation').append(article);
}
let latestQuestion = '';
function message(event) {
  let data; try { data = JSON.parse(event.data); } catch { return; }
  if (data.type !== 'guide') return;
  if (data.event === 'connected') state('You’re connected', data.greeting);
  if (data.event === 'listening') state('Listening', 'Ask your SCU question.');
  if (data.event === 'thinking') { latestQuestion = data.transcript; state('Checking public sources', data.transcript); }
  if (data.event === 'answer') { showAnswer(data.answer, latestQuestion); state('Here’s what I found', 'You can interrupt and ask a follow-up.'); }
  if (data.event === 'error') state('Something went wrong', data.message);
  if (data.event === 'ended') { end(); state('Call ended', 'You can start another call.'); }
}
function buttons() { $('call').disabled = active; $('mute').disabled = !active; $('hangup').disabled = !active; $('orb').classList.toggle('active',active); for (const role of Object.keys(choices)) $(role).disabled = active || !choices[role].switching; }
async function end() {
  epoch++; active = false; clearInterval(ping); clearTimeout(timer);
  if (callIdentity && closeToken) { const id=callIdentity, token=closeToken; callIdentity=null; closeToken=null; request('/api/v1/calls/'+encodeURIComponent(id)+'/close',{token}).catch(()=>{}); }
  if (stream) stream.getTracks().forEach(t=>t.stop()); stream=null;
  if (pc) { pc.onconnectionstatechange=null; pc.close(); } pc=null; channel=null; $('audio').srcObject=null; $('mute').textContent='Mute'; buttons();
}
$('call').onclick = async () => {
  const attempt=++epoch; active=true; buttons(); state('Connecting', 'Allow microphone access to begin.');
  try {
    const acquired=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});
    if(attempt!==epoch){acquired.getTracks().forEach(t=>t.stop());return;}
    stream=acquired;
    pc=new RTCPeerConnection({iceServers});
    const peer=pc;
    for (const track of stream.getAudioTracks()) pc.addTrack(track,stream);
    pc.ontrack=e=>{ $('audio').srcObject=new MediaStream([e.track]); $('audio').play().catch(()=>state('Tap Start to enable audio')); };
    channel=pc.createDataChannel('guide'); channel.onmessage=message;
    channel.onopen=()=>{ping=setInterval(()=>{if(channel && channel.readyState==='open')channel.send('ping');},1000);};
    pc.onconnectionstatechange=()=>{if(pc && ['failed','closed','disconnected'].includes(pc.connectionState)){end();state('Call disconnected','Please start a new call.');}};
    await pc.setLocalDescription(await pc.createOffer());
    if (pc.iceGatheringState !== 'complete') await new Promise((resolve,reject)=>{ const timeout=setTimeout(()=>reject(new Error('Microphone connection timed out')),7000); peer.onicegatheringstatechange=()=>{if(peer.iceGatheringState==='complete'){clearTimeout(timeout);resolve();}}; });
    if(attempt!==epoch)return;
    const answer=await request('/api/v1/offer',{sdp:pc.localDescription.sdp,type:'offer',selection:selection()});
    if(attempt!==epoch){request("/api/v1/calls/"+encodeURIComponent(answer.call_id)+"/close",{token:answer.close_token}).catch(()=>{});return;}
    callIdentity=answer.call_id; closeToken=answer.close_token;
    await pc.setRemoteDescription({sdp:answer.sdp,type:answer.type});
    timer=setTimeout(()=>{end();state('Call limit reached','Calls last up to three minutes.');},180000);
  } catch(e){if(attempt===epoch){await end();state('Unable to connect',e.message);}}
};
$('hangup').onclick=()=>{end();state('Call ended','Your microphone is off.');};
$('mute').onclick=()=>{if(!stream)return;const track=stream.getAudioTracks()[0];track.enabled=!track.enabled;$('mute').textContent=track.enabled?'Mute':'Unmute';};
$('text-form').onsubmit=async e=>{e.preventDefault();$('ask').disabled=true;const question=$('question').value;state('Checking public sources');try{const answer=await request('/api/v1/ask',{question,selection:selection()});showAnswer(answer,question);state('Ready for another question');}catch(error){state('Unable to answer',error.message);}finally{$('ask').disabled=false;}};
window.addEventListener('pagehide',()=>{if(callIdentity&&closeToken){navigator.sendBeacon('/api/v1/calls/'+encodeURIComponent(callIdentity)+'/close',new Blob([JSON.stringify({token:closeToken})],{type:'application/json'}));}if(stream)stream.getTracks().forEach(t=>t.stop());});
(async()=>{try{const response=await fetch('/api/v1/providers');if(!response.ok)throw Error('Server unavailable');const data=await response.json();iceServers=(data.ice_servers||[]).map(url=>({urls:url}));for(const [role,providers]of Object.entries(data.roles)){const label=document.createElement('label');label.textContent=role.toUpperCase();const select=document.createElement('select');select.id=role;select.setAttribute('aria-label',role+' provider');for(const provider of providers){const option=document.createElement('option');option.value=provider.provider;option.textContent=provider.provider+' · '+provider.model+(provider.available?'':' · needs key');option.disabled=!provider.available;select.append(option);}select.value=data.default_selection?.[role]||providers.find(p=>p.available)?.provider||providers[0].provider;select.disabled=!data.switching_enabled;choices[role]={switching:data.switching_enabled};label.append(select);$('providers').append(label);}}catch(e){state('Service unavailable',e.message);$('call').disabled=true;$('ask').disabled=true;}})();
