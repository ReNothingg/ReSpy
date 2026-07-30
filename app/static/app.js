(() => {
  const messageList = document.querySelector('[data-scroll-bottom="true"]');
  if (messageList?.lastElementChild) {
    requestAnimationFrame(() => {
      messageList.lastElementChild.scrollIntoView({ block: "end" });
    });
  }

  const webApp = window.Telegram?.WebApp;
  if (!webApp) return;

  const root = document.documentElement;
  const applyTheme = () => {
    root.dataset.telegram = "true";
    root.dataset.theme = webApp.colorScheme || "light";
    const color = webApp.themeParams?.bg_color;
    if (color) {
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", color);
    }
  };

  applyTheme();
  webApp.onEvent?.("themeChanged", applyTheme);
  webApp.ready();
  webApp.expand();
  webApp.setHeaderColor?.("bg_color");
  webApp.setBackgroundColor?.("bg_color");
  webApp.setBottomBarColor?.("bottom_bar_bg_color");

  const isNestedPage = location.pathname !== "/" && location.pathname !== "/login";
  if (isNestedPage && webApp.BackButton) {
    webApp.BackButton.show();
    webApp.BackButton.onClick(() => history.back());
  } else {
    webApp.BackButton?.hide();
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest("a, button, select")) {
      webApp.HapticFeedback?.selectionChanged();
    }
  });

  const form = document.querySelector(".login-form");
  const status = document.querySelector(".telegram-auth-status");
  if (!form || !webApp.initData) return;

  form.hidden = true;
  if (status) status.hidden = false;
  const body = new URLSearchParams({ init_data: webApp.initData });
  fetch("/telegram-auth", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
    credentials: "same-origin",
  }).then((response) => {
    if (!response.ok) throw new Error("telegram-auth-failed");
    const destination = form.querySelector('input[name="next"]')?.value || "/";
    location.replace(destination);
  }).catch(() => {
    form.hidden = false;
    if (status) status.hidden = true;
  });
})();
