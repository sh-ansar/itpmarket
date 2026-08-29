(() => {
  const messages = {
    ru: {
      task_queued: "В очереди",
      task_queued_message: "Операция ожидает запуска в очереди",
    },
    kk: {
      task_queued: "Кезекте",
      task_queued_message: "Операция кезекте іске қосылуын күтуде",
    },
    en: {
      task_queued: "Queued",
      task_queued_message: "The operation is waiting in the queue",
    },
  };
  for (const [language, values] of Object.entries(messages)) {
    if (window.ITP_LOCALES?.[language]) Object.assign(window.ITP_LOCALES[language], values);
  }
})();
