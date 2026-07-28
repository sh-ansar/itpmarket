(()=>{
  'use strict';
  const locales=window.ITP_LOCALES||{};
  const textMaps=window.ITP_TEXT_TRANSLATIONS||{};
  const help=window.ITP_HELP_CONTENT||{};
  const listeners={locale:new Set(),theme:new Set()};
  const sourceText=new WeakMap();
  const media=window.matchMedia?.('(prefers-color-scheme: dark)');
  const supported=['ru','kk','en'];
  let locale=supported.includes(localStorage.getItem('itp_lang'))?localStorage.getItem('itp_lang'):'ru';
  let preference=['system','light','dark'].includes(localStorage.getItem('itp_theme'))?localStorage.getItem('itp_theme'):'system';
  const effective=()=>preference==='system'?(media?.matches?'dark':'light'):preference;
  const t=(key,fallback='')=>locales[locale]?.[key]??locales.ru?.[key]??fallback??key;
  function applyTheme(next,{store=true,emit=true}={}){
    preference=['system','light','dark'].includes(next)?next:'system';
    const value=effective();
    document.documentElement.dataset.theme=value;
    document.documentElement.dataset.themePreference=preference;
    document.documentElement.style.colorScheme=value;
    if(store)localStorage.setItem('itp_theme',preference);
    document.querySelectorAll('[data-theme-option]').forEach(el=>el.classList.toggle('active',el.dataset.themeOption===preference));
    document.querySelectorAll('[data-theme-toggle]').forEach(el=>{el.dataset.theme=value;el.setAttribute('aria-pressed',String(value==='dark'));el.title=t(value==='dark'?'theme_light':'theme_dark');});
    if(emit)listeners.theme.forEach(fn=>fn(preference,value));
    return value;
  }
  const preserveWhitespace=(original,replacement)=>original.replace(original.trim(),replacement);
  function patternTranslate(source){
    const patterns={
      kk:[
        [/^Обновлено за ([\d.,]+) сек$/,m=>`${m[1]} сек ішінде жаңартылды`],[/^(\d+) поз\. · (\d+) ед\.\/мес\.$/,m=>`${m[1]} позиция · айына ${m[2]} дана`],[/^(\d+) из (\d+) · ([\d.,]+)%$/,m=>`${m[1]} / ${m[2]} · ${m[3]}%`],[/^Осталось ~(.+)$/,m=>`Қалды ~${m[1]}`],[/^(\d+) строк$/,m=>`${m[1]} жол`],[/^(\d+) на проверке$/,m=>`${m[1]} тексеруде`],[/^Следующий: (.+)$/,m=>`Келесі: ${m[1]}`],[/^Последний: (.+)$/,m=>`Соңғы: ${m[1]}`],[/^Каждые ([\d.,]+) ч$/,m=>`Әр ${m[1]} сағат`],[/^По дням недели · (.+)$/,m=>`Апта күндері · ${m[1]}`],[/^Ежедневно · (.+)$/,m=>`Күн сайын · ${m[1]}`],[/^(\d+) продавцов$/,m=>`${m[1]} сатушы`],[/^(\d+) позиций$/,m=>`${m[1]} позиция`],[/^(\d+) из (\d+) с ценой$/,m=>`${m[1]} / ${m[2]} бағамен`]
      ],
      en:[
        [/^Обновлено за ([\d.,]+) сек$/,m=>`Updated in ${m[1]} sec`],[/^(\d+) поз\. · (\d+) ед\.\/мес\.$/,m=>`${m[1]} products · ${m[2]} units/month`],[/^(\d+) из (\d+) · ([\d.,]+)%$/,m=>`${m[1]} of ${m[2]} · ${m[3]}%`],[/^Осталось ~(.+)$/,m=>`About ${m[1]} remaining`],[/^(\d+) строк$/,m=>`${m[1]} rows`],[/^(\d+) на проверке$/,m=>`${m[1]} under review`],[/^Следующий: (.+)$/,m=>`Next: ${m[1]}`],[/^Последний: (.+)$/,m=>`Last: ${m[1]}`],[/^Каждые ([\d.,]+) ч$/,m=>`Every ${m[1]} hours`],[/^По дням недели · (.+)$/,m=>`Selected weekdays · ${m[1]}`],[/^Ежедневно · (.+)$/,m=>`Daily · ${m[1]}`],[/^(\d+) продавцов$/,m=>`${m[1]} sellers`],[/^(\d+) позиций$/,m=>`${m[1]} positions`],[/^(\d+) из (\d+) с ценой$/,m=>`${m[1]} of ${m[2]} priced`]
      ]
    };
    for(const [re,fn] of patterns[locale]||[]){const match=source.match(re);if(match)return fn(match);}
    return null;
  }
  function translateValue(source){if(locale==='ru')return source;return textMaps[locale]?.[source]??patternTranslate(source)??source;}
  function translateTextNode(node){
    const raw=sourceText.get(node)??node.nodeValue;
    if(!sourceText.has(node))sourceText.set(node,raw);
    const trimmed=raw.trim();if(!trimmed)return;
    if(node.parentElement?.closest('[data-i18n],[data-no-auto-i18n]'))return;
    const translated=translateValue(trimmed);node.nodeValue=preserveWhitespace(raw,translated);
  }
  function translateTree(root=document){
    if(root.nodeType===Node.TEXT_NODE){translateTextNode(root);return;}
    const scope=root.nodeType===Node.ELEMENT_NODE?root:document;
    scope.querySelectorAll?.('[data-i18n]').forEach(el=>{const value=t(el.dataset.i18n,el.textContent);if(value!=null)el.textContent=value;});
    scope.querySelectorAll?.('[data-i18n-placeholder]').forEach(el=>{el.placeholder=t(el.dataset.i18nPlaceholder,el.placeholder);});
    scope.querySelectorAll?.('[data-i18n-title]').forEach(el=>{el.title=t(el.dataset.i18nTitle,el.title);});
    scope.querySelectorAll?.('[data-i18n-aria]').forEach(el=>{el.setAttribute('aria-label',t(el.dataset.i18nAria,el.getAttribute('aria-label')||''));});
    scope.querySelectorAll?.('[data-i18n-tooltip]').forEach(el=>{const value=t(el.dataset.i18nTooltip,el.dataset.tooltip||'');el.dataset.tooltip=value;el.title=value;});
    const walker=document.createTreeWalker(scope,NodeFilter.SHOW_TEXT,{acceptNode:n=>n.parentElement?.closest('script,style')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});
    let node;while((node=walker.nextNode()))translateTextNode(node);
    document.documentElement.lang=locale==='kk'?'kk':locale;
  }
  function setLocale(next,{store=true,emit=true}={}){
    locale=supported.includes(next)?next:'ru';if(store)localStorage.setItem('itp_lang',locale);
    document.querySelectorAll('[data-lang]').forEach(el=>el.classList.toggle('active',el.dataset.lang===locale));
    translateTree(document.body);applyTheme(preference,{store:false,emit:false});
    if(emit)listeners.locale.forEach(fn=>fn(locale));return locale;
  }
  function bind(root=document){
    root.querySelectorAll('[data-lang]').forEach(el=>{if(!el.dataset.uiBound){el.dataset.uiBound='1';el.addEventListener('click',()=>setLocale(el.dataset.lang));}});
    root.querySelectorAll('[data-theme-toggle]').forEach(el=>{if(!el.dataset.uiBound){el.dataset.uiBound='1';el.addEventListener('click',()=>applyTheme(effective()==='dark'?'light':'dark'));}});
    root.querySelectorAll('[data-theme-option]').forEach(el=>{if(!el.dataset.uiBound){el.dataset.uiBound='1';el.addEventListener('click',()=>applyTheme(el.dataset.themeOption));}});
  }
  const observer=new MutationObserver(records=>{for(const record of records){for(const node of record.addedNodes){if(node.nodeType===1||node.nodeType===3){translateTree(node);if(node.nodeType===1)bind(node);}}}});
  media?.addEventListener?.('change',()=>{if(preference==='system')applyTheme('system',{store:false});});
  window.ITPUI={t,getLocale:()=>locale,setLocale,getTheme:()=>preference,getEffectiveTheme:effective,setTheme:applyTheme,translateTree,bind,onLocale:fn=>listeners.locale.add(fn),onTheme:fn=>listeners.theme.add(fn),helpFor:page=>(help[locale]||help.ru||{})[page]};
  applyTheme(preference,{store:false,emit:false});
  document.addEventListener('DOMContentLoaded',()=>{bind();setLocale(locale,{store:false,emit:false});observer.observe(document.body,{childList:true,subtree:true});});
})();
