(function(){"use strict";
var root=document.documentElement;
function mode(m){root.setAttribute("data-mode",m);
document.querySelectorAll(".modes button").forEach(function(b){
b.setAttribute("aria-pressed",String(b.getAttribute("data-mode")===m));});
var bar=document.querySelector(".toolbar");
if(bar)root.style.setProperty("--tb",bar.offsetHeight+"px");}
mode("queue");
document.querySelectorAll(".modes button").forEach(function(b){b.addEventListener("click",
function(){mode(b.getAttribute("data-mode"));});});
document.addEventListener("click",function(e){
if(e.target.closest("a[data-unit]")&&root.getAttribute("data-mode")!=="all")mode("all");},true);
})();
