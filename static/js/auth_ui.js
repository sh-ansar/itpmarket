(()=>{'use strict';const copy=document.querySelector('#copyRecovery');if(copy)copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(document.querySelector('#recoveryCode').textContent);copy.textContent=window.ITPUI?.t('copied','Скопировано')||'Скопировано';setTimeout(()=>copy.textContent=window.ITPUI?.t('copy','Копировать')||'Копировать',1800)}catch(e){}});})();


/* Login password visibility v1 */
(function () {
  "use strict";

  const input=document.querySelector(
    'input[name="password"][autocomplete="current-password"]'
  );

  if(
    !input
    ||input.dataset.passwordEyeReady==='true'
  ){
    return;
  }

  input.dataset.passwordEyeReady='true';

  const wrapper=document.createElement(
    'span'
  );

  wrapper.className=
    'auth-password-control';

  input.parentNode.insertBefore(
    wrapper,
    input
  );

  wrapper.appendChild(
    input
  );

  const button=document.createElement(
    'button'
  );

  button.type='button';

  button.className=
    'auth-password-toggle';

  button.setAttribute(
    'aria-label',
    'Show password'
  );

  button.innerHTML=
    '<img src="/static/icons/eye.svg" alt="">';

  button.addEventListener(
    'click',
    ()=>{
      const visible=
        input.type==='text';

      input.type=
        visible
          ?'password'
          :'text';

      button.setAttribute(
        'aria-label',
        visible
          ?'Show password'
          :'Hide password'
      );

      input.focus();
    }
  );

  wrapper.appendChild(
    button
  );
})();
