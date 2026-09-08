const accessMessage = document.querySelector("#access-message");
const accessStatus = document.querySelector("#access-status");
const settingsContent = document.querySelector("#settings-content");
const settingsStatus = document.querySelector("#settings-status");
const form = document.querySelector("#preferences");

function showAccessMessage(message) {
  settingsContent.hidden = true;
  accessStatus.textContent = message;
  accessMessage.hidden = false;
}

function showSettingsStatus(message) {
  settingsStatus.textContent = message;
}

function timeForInput(value) {
  return value ? value.slice(0, 5) : "";
}

async function startLogin(requestToken) {
  history.replaceState(null, "", "/settings");
  showAccessMessage("Verifying your settings link…");
  const response = await fetch("/settings/auth/telegram/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_token: requestToken }),
  });
  if (!response.ok) {
    showAccessMessage("This settings link is invalid, expired, or already used.");
    return;
  }
  const payload = await response.json();
  window.location.replace(payload.authorization_url);
}

async function loadPreferences() {
  const response = await fetch("/settings/preferences");
  if (!response.ok) {
    showAccessMessage("Request a new settings link from Atlas in Telegram.");
    return;
  }

  const preferences = await response.json();
  document.querySelector("#timezone").value = preferences.timezone;
  document.querySelector("#quiet-hours-start").value = timeForInput(
    preferences.quiet_hours_start,
  );
  document.querySelector("#quiet-hours-end").value = timeForInput(preferences.quiet_hours_end);
  document.querySelector("#notifications-enabled").checked = preferences.notifications_enabled;
  document.querySelector("#notifications-on-weekends").checked =
    preferences.notifications_on_weekends;
  accessMessage.hidden = true;
  settingsContent.hidden = false;
  showSettingsStatus("Signed in through Telegram.");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showSettingsStatus("Saving…");
  const response = await fetch("/settings/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      timezone: document.querySelector("#timezone").value,
      quiet_hours_start: document.querySelector("#quiet-hours-start").value || null,
      quiet_hours_end: document.querySelector("#quiet-hours-end").value || null,
      notifications_enabled: document.querySelector("#notifications-enabled").checked,
      notifications_on_weekends: document.querySelector("#notifications-on-weekends").checked,
    }),
  });
  showSettingsStatus(
    response.ok ? "Settings saved." : "Atlas could not save those settings.",
  );
});

document.querySelector("#logout").addEventListener("click", async () => {
  await fetch("/settings/session", { method: "DELETE" });
  showAccessMessage("Logged out. Request a new settings link from Atlas when needed.");
});

const requestToken = new URLSearchParams(window.location.hash.slice(1)).get("login");
if (requestToken) {
  startLogin(requestToken).catch(() =>
    showAccessMessage("Telegram login could not be started."),
  );
} else {
  loadPreferences().catch(() =>
    showAccessMessage("Atlas settings are temporarily unavailable."),
  );
}
