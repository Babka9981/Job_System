const test = require("node:test");
const assert = require("node:assert/strict");
const { bindDisableOnSubmit } = require("../form-submit.js");

function fixture(valid=true) {
  const buttons = ["Сохранить", "Подтвердить"].map(text => ({disabled:false,textContent:text,attributes:new Map(),setAttribute(name,value){this.attributes.set(name,String(value))}}));
  let submit;
  const form={checkValidity:()=>valid,querySelectorAll:selector=>selector==='button[type="submit"]'?buttons:[],addEventListener:(name,listener)=>{if(name==="submit")submit=listener}};
  const root={querySelectorAll:selector=>selector==="form[data-disable-on-submit]"?[form]:[]};
  bindDisableOnSubmit(root);
  return {buttons,submit:()=>submit({type:"submit"})};
}

test("valid generic submit disables every submit button without changing text",()=>{const f=fixture(true);f.submit();for(const button of f.buttons){assert.equal(button.disabled,true);assert.equal(button.attributes.get("aria-disabled"),"true")}assert.deepEqual(f.buttons.map(button=>button.textContent),["Сохранить","Подтвердить"])});
test("invalid generic submit keeps controls enabled",()=>{const f=fixture(false);f.submit();for(const button of f.buttons){assert.equal(button.disabled,false);assert.equal(button.attributes.has("aria-disabled"),false)}});
