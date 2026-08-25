(function () {
  "use strict";

  var form = document.getElementById("registrationForm");

  if (!form) {
    return;
  }

  var sections = Array.from(
    form.querySelectorAll("[data-registration-step]")
  );

  if (!sections.length) {
    return;
  }

  var previous = document.getElementById("registrationPrevious");
  var next = document.getElementById("registrationNext");
  var number = document.getElementById("registrationStepNumber");
  var title = document.getElementById("registrationStepTitle");
  var status = document.getElementById("registrationStepStatus");
  var progress = document.getElementById("registrationProgressBar");

  var marketplaceBox = document.getElementById(
    "registrationMarketplaces"
  );
  var marketplaceError = document.getElementById(
    "marketplaceSelectionError"
  );

  var productsInput = document.getElementById(
    "registrationProducts"
  );
  var plansBox = document.getElementById("registrationPlans");
  var recommendation = document.getElementById(
    "planRecommendationText"
  );
  var marketplaceSummary = document.getElementById(
    "planMarketplaceSummary"
  );

  var emailInput = document.getElementById("registrationEmail");
  var accountEmail = document.getElementById(
    "registrationAccountEmail"
  );

  var legalAddress = document.getElementById(
    "registrationLegalAddress"
  );
  var actualAddress = document.getElementById(
    "registrationActualAddress"
  );
  var sameAddress = document.getElementById(
    "registrationSameAddress"
  );

  var password = document.getElementById(
    "registrationPassword"
  );
  var passwordConfirm = document.getElementById(
    "registrationPasswordConfirm"
  );

  var current = 0;
  var selectedAtLoad = form.querySelector(
    'input[name="plan_code"]:checked'
  );
  var planTouched = Boolean(selectedAtLoad);

  function locale() {
    return (
      localStorage.getItem("itp_lang") ||
      document.documentElement.lang ||
      "ru"
    );
  }

  function tr(key, fallback) {
    var language = locale();
    var values = window.ITP_PUBLIC_LOCALES || {};

    return (
      (values[language] && values[language][key]) ||
      (values.ru && values.ru[key]) ||
      fallback
    );
  }

  function interpolate(text, values) {
    return Object.keys(values).reduce(function (result, key) {
      return result.replaceAll(
        "{" + key + "}",
        String(values[key])
      );
    }, text);
  }

  function sectionTitle(section) {
    var key = section.dataset.stepTitleKey || "";
    var heading = section.querySelector("h2");

    return tr(
      key,
      heading ? heading.textContent.trim() : ""
    );
  }

  function updateGuide() {
    sections.forEach(function (section, index) {
      var active = index === current;

      section.hidden = !active;
      section.classList.toggle(
        "is-current-step",
        active
      );
    });

    if (number) {
      number.textContent = interpolate(
        tr(
          "register_guide_step",
          "Step {current} of {total}"
        ),
        {
          current: current + 1,
          total: sections.length,
        }
      );
    }

    if (title) {
      title.textContent = sectionTitle(
        sections[current]
      );
    }

    if (status) {
      status.textContent = (
        current === sections.length - 1
          ? tr(
              "register_guide_review",
              "review and submit"
            )
          : tr(
              "register_guide_fill",
              "complete this step"
            )
      );
    }

    if (progress) {
      progress.style.width = (
        ((current + 1) / sections.length) * 100
      ) + "%";
    }

    if (previous) {
      previous.disabled = current === 0;
    }

    if (next) {
      next.textContent = (
        current === sections.length - 1
          ? tr(
              "register_submit",
              "Submit application"
            )
          : tr(
              "register_guide_next",
              "Next"
            )
      );
    }
  }

  function go(index) {
    current = Math.max(
      0,
      Math.min(index, sections.length - 1)
    );

    updateGuide();

    sections[current].scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }

  function marketplaceValid() {
    if (!marketplaceBox) {
      return true;
    }

    var valid = Boolean(
      marketplaceBox.querySelector(
        'input[name="marketplaces"]:checked'
      )
    );

    marketplaceBox.classList.toggle(
      "invalid",
      !valid
    );

    if (marketplaceError) {
      marketplaceError.hidden = valid;
    }

    return valid;
  }

  function refreshPasswordValidation() {
    if (!password || !passwordConfirm) {
      return;
    }

    var mismatch = (
      passwordConfirm.value &&
      password.value !== passwordConfirm.value
    );

    passwordConfirm.setCustomValidity(
      mismatch
        ? tr(
            "register_password_mismatch",
            "Passwords do not match."
          )
        : ""
    );
  }

  function invalidControlFor(index) {
    var section = sections[index];

    if (
      section.dataset.stepCode === "marketplaces" &&
      !marketplaceValid()
    ) {
      return (
        section.querySelector(
          'input[name="marketplaces"]'
        ) || section
      );
    }

    refreshPasswordValidation();

    var controls = Array.from(
      section.querySelectorAll(
        'input:not([type="hidden"]),select,textarea'
      )
    );

    return controls.find(function (control) {
      return !control.checkValidity();
    }) || null;
  }

  function validateStep(index) {
    var invalid = invalidControlFor(index);

    if (!invalid) {
      return true;
    }

    go(index);

    window.setTimeout(function () {
      if (
        invalid.reportValidity &&
        invalid.checkValidity &&
        !invalid.checkValidity()
      ) {
        invalid.reportValidity();
      }

      if (invalid.focus) {
        invalid.focus();
      }
    }, 0);

    return false;
  }

  function validateAll() {
    for (
      var index = 0;
      index < sections.length;
      index += 1
    ) {
      if (!validateStep(index)) {
        return false;
      }
    }

    return true;
  }

  function syncEmail() {
    if (!emailInput || !accountEmail) {
      return;
    }

    accountEmail.value = emailInput.value.trim();
  }

  function syncAddress() {
    if (
      !sameAddress ||
      !legalAddress ||
      !actualAddress
    ) {
      return;
    }

    if (sameAddress.checked) {
      actualAddress.value = legalAddress.value;
      actualAddress.readOnly = true;
      actualAddress.classList.add("is-synced");
    } else {
      actualAddress.readOnly = false;
      actualAddress.classList.remove("is-synced");
    }
  }

  function planEntries() {
    if (!plansBox) {
      return [];
    }

    return Array.from(
      plansBox.querySelectorAll(
        ".subscription-plan-card"
      )
    ).map(function (card) {
      var rawLimit = card.dataset.positionLimit || "";
      var parsedLimit = (
        rawLimit === ""
          ? Number.POSITIVE_INFINITY
          : Number(rawLimit)
      );

      return {
        card: card,
        input: card.querySelector(
          'input[name="plan_code"]'
        ),
        code: card.dataset.planCode || "",
        limit: parsedLimit,
        order: Number(
          card.dataset.displayOrder || 0
        ),
        name: (
          card.querySelector(
            ".integration-head strong"
          ) || {}
        ).textContent || card.dataset.planCode || "",
      };
    });
  }

  function selectedMarketplaceCount() {
    if (!marketplaceBox) {
      return 0;
    }

    return marketplaceBox.querySelectorAll(
      'input[name="marketplaces"]:checked'
    ).length;
  }

  function updateRecommendation() {
    var products = Number(
      productsInput ? productsInput.value : 0
    );

    var plans = planEntries();

    plans.forEach(function (entry) {
      entry.card.classList.remove(
        "is-recommended"
      );
    });

    if (
      !Number.isFinite(products) ||
      products < 1 ||
      !plans.length
    ) {
      if (recommendation) {
        recommendation.textContent = tr(
          "register_plan_recommendation_wait",
          "Enter the estimated number of products."
        );
      }

      if (marketplaceSummary) {
        marketplaceSummary.textContent = "";
      }

      return;
    }

    var ordered = plans.slice().sort(
      function (left, right) {
        if (left.limit !== right.limit) {
          return left.limit - right.limit;
        }

        return left.order - right.order;
      }
    );

    var recommended = ordered.find(
      function (entry) {
        return entry.limit >= products;
      }
    );

    if (!recommended) {
      recommended = ordered[
        ordered.length - 1
      ];
    }

    recommended.card.classList.add(
      "is-recommended"
    );

    if (
      !planTouched &&
      recommended.input
    ) {
      recommended.input.checked = true;
    }

    if (recommendation) {
      if (
        Number.isFinite(recommended.limit) &&
        products > recommended.limit
      ) {
        recommendation.textContent = interpolate(
          tr(
            "register_plan_recommendation_extra",
            "Recommended {plan} plus at least {extra} extra positions."
          ),
          {
            plan: recommended.name.trim(),
            extra: Math.ceil(
              products - recommended.limit
            ),
          }
        );
      } else {
        recommendation.textContent = interpolate(
          tr(
            "register_plan_recommendation_fit",
            "{plan} fits the entered catalog size."
          ),
          {
            plan: recommended.name.trim(),
          }
        );
      }
    }

    if (marketplaceSummary) {
      marketplaceSummary.textContent = interpolate(
        tr(
          "register_plan_per_marketplace",
          "{count} marketplaces - the limit applies separately to each."
        ),
        {
          count: selectedMarketplaceCount(),
        }
      );
    }
  }

  function refreshLocale() {
    updateGuide();
    updateRecommendation();

    form.querySelectorAll(
      "[data-toggle-password]"
    ).forEach(function (button) {
      var target = document.getElementById(
        button.dataset.togglePassword
      );

      var visible = (
        target &&
        target.type === "text"
      );

      var text = tr(
        visible
          ? "register_hide_password"
          : "register_show_password",
        visible
          ? "Hide password"
          : "Show password"
      );

      button.setAttribute(
        "aria-label",
        text
      );
      button.title = text;
    });
  }

  if (previous) {
    previous.addEventListener(
      "click",
      function () {
        go(current - 1);
      }
    );
  }

  if (next) {
    next.addEventListener(
      "click",
      function () {
        if (
          current === sections.length - 1
        ) {
          if (validateAll()) {
            form.requestSubmit();
          }

          return;
        }

        if (validateStep(current)) {
          go(current + 1);
        }
      }
    );
  }

  if (marketplaceBox) {
    marketplaceBox.addEventListener(
      "change",
      function () {
        marketplaceValid();
        updateRecommendation();
      }
    );
  }

  if (plansBox) {
    plansBox.addEventListener(
      "change",
      function (event) {
        if (
          event.target.matches(
            'input[name="plan_code"]'
          )
        ) {
          planTouched = true;
        }
      }
    );
  }

  if (productsInput) {
    productsInput.addEventListener(
      "input",
      updateRecommendation
    );
  }

  if (emailInput) {
    emailInput.addEventListener(
      "input",
      syncEmail
    );
  }

  if (sameAddress) {
    sameAddress.addEventListener(
      "change",
      syncAddress
    );
  }

  if (legalAddress) {
    legalAddress.addEventListener(
      "input",
      function () {
        if (
          sameAddress &&
          sameAddress.checked
        ) {
          syncAddress();
        }
      }
    );
  }

  if (
    sameAddress &&
    legalAddress &&
    actualAddress &&
    legalAddress.value &&
    legalAddress.value === actualAddress.value
  ) {
    sameAddress.checked = true;
  }

  if (password) {
    password.addEventListener(
      "input",
      refreshPasswordValidation
    );
  }

  if (passwordConfirm) {
    passwordConfirm.addEventListener(
      "input",
      refreshPasswordValidation
    );
  }

  form.querySelectorAll(
    "[data-toggle-password]"
  ).forEach(function (button) {
    button.addEventListener(
      "click",
      function () {
        var target = document.getElementById(
          button.dataset.togglePassword
        );

        if (!target) {
          return;
        }

        target.type = (
          target.type === "password"
            ? "text"
            : "password"
        );

        refreshLocale();
        target.focus();
      }
    );
  });

  document.querySelectorAll(
    "[data-public-lang]"
  ).forEach(function (button) {
    button.addEventListener(
      "click",
      function () {
        var localeInput = document.getElementById(
          "registrationLocale"
        );

        if (localeInput) {
          localeInput.value = (
            button.dataset.publicLang || "ru"
          );
        }
      }
    );
  });

  document.addEventListener(
    "itp:public-locale",
    refreshLocale
  );

  form.addEventListener(
    "submit",
    function (event) {
      if (!validateAll()) {
        event.preventDefault();
      }
    }
  );

  var storedLocale = (
    localStorage.getItem("itp_lang") || "ru"
  );

  var localeInput = document.getElementById(
    "registrationLocale"
  );

  if (localeInput) {
    localeInput.value = storedLocale;
  }

  syncEmail();
  syncAddress();
  marketplaceValid();
  updateRecommendation();
  refreshLocale();
})();
