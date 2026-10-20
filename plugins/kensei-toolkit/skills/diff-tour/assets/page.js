(function(){"use strict";
var root=document.documentElement,KEY="diff-tour-view";
function show(v,save){root.setAttribute("data-view",v);
document.querySelectorAll(".seg button").forEach(function(b){
b.setAttribute("aria-pressed",String(b.getAttribute("data-view")===v));});
if(save){try{localStorage.setItem(KEY,v);}catch(e){}}}
var v=null;try{v=localStorage.getItem(KEY);}catch(e){}
if(v!=="split"&&v!=="unified"){v=window.matchMedia&&matchMedia("(max-width: 760px)").matches
?"unified":"split";}
if(!document.querySelector("table.split"))v="unified";
show(v,false);
document.querySelectorAll(".seg button").forEach(function(b){b.addEventListener("click",
function(){show(b.getAttribute("data-view"),true);});});
var bar=document.querySelector(".toolbar");
function fit(){if(bar)root.style.setProperty("--tb",bar.offsetHeight+"px");}
fit();window.addEventListener("resize",fit);
document.addEventListener("toggle",function(e){var d=e.target;
if(d.open||!d.classList||!d.classList.contains("file"))return;
if(d.getBoundingClientRect().top<(bar?bar.offsetHeight:0))d.scrollIntoView({block:"start"});},true);
document.addEventListener("click",function(e){var a=e.target.closest("a[data-unit]");if(!a)return;
var sel='[data-unit="'+a.getAttribute("data-unit")+'"]';
var t=document.querySelector("table."+root.getAttribute("data-view")+" "+sel)||
document.querySelector("table.solo "+sel)||document.querySelector(".prelude"+sel);
if(!t)return;e.preventDefault();var d=t.closest("details");if(d)d.open=true;
t.scrollIntoView({block:"center"});});
function lines(html){var out=[],open=[],cur="",re=/<span[^>]*>|<\/span>|\n|[^<\n]+/g,m;
while((m=re.exec(html))){var t=m[0];
if(t==="\n"){out.push(cur+Array(open.length+1).join("</span>"));cur=open.join("");}
else if(t.charAt(0)==="<"){if(t.charAt(1)==="/")open.pop();else open.push(t);cur+=t;}
else{cur+=t;}}
out.push(cur+Array(open.length+1).join("</span>"));return out;}
function paint(seq,lang){if(!seq.length)return;var out;
try{out=lines(hljs.highlight(seq.map(function(x){return x[0].textContent;}).join("\n"),
{language:lang,ignoreIllegals:true}).value);}catch(e){return;}
if(out.length!==seq.length)return;
seq.forEach(function(x,i){if(x[1])x[0].innerHTML=out[i];});}
if(window.hljs){document.querySelectorAll("table.diff[data-lang]").forEach(function(t){
var lang=t.getAttribute("data-lang");if(!hljs.getLanguage(lang))return;
var o=[],n=[];function flush(){paint(o,lang);paint(n,lang);o=[];n=[];}
Array.prototype.forEach.call(t.rows,function(tr){if(tr.classList.contains("hunk")){flush();return;}
tr.querySelectorAll("span.t").forEach(function(s){var side=s.getAttribute("data-s");
if(side==="o"){o.push([s,true]);}else if(side==="n"){n.push([s,true]);}
else{o.push([s,false]);n.push([s,true]);}});});flush();});}
})();
