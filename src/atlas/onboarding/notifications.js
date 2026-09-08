const accessMessage = document.querySelector("#access-message");
const accessStatus = document.querySelector("#access-status");
const notificationSettingsContent = document.querySelector("#settings-content");
const notificationSettingsStatus = document.querySelector("#settings-status");
const form = document.querySelector("#preferences");
const submitButton = document.querySelector("#submit-button");
const submitLabel = document.querySelector("#submit-label");
const timezoneSelect = document.querySelector("#timezone");
const telegramAccountName = document.querySelector("#telegram-account-name");
const telegramAccountDetail = document.querySelector("#telegram-account-detail");
const sessionNoteLabel = document.querySelector("#session-note-label");
const notificationWindowStart = document.querySelector("#notification-window-start");
const notificationWindowEnd = document.querySelector("#notification-window-end");
const notificationWindowDuration = document.querySelector("#notification-window-duration");

function showAccessMessage(message) {
  document.body.classList.add("access-only");
  notificationSettingsContent.hidden = true;
  accessStatus.textContent = message;
  accessMessage.hidden = false;
}

function showSettingsStatus(message) {
  notificationSettingsStatus.textContent = message;
}

function showSettings() {
  document.body.classList.remove("access-only");
  accessMessage.hidden = true;
  notificationSettingsContent.hidden = false;
}

function timeForInput(value) {
  return value ? value.slice(0, 5) : "";
}

function updateNotificationWindowDuration() {
  if (!notificationWindowStart.value && !notificationWindowEnd.value) {
    notificationWindowDuration.textContent = "No window configured";
    return;
  }
  if (!notificationWindowStart.value || !notificationWindowEnd.value) {
    notificationWindowDuration.textContent = "Set both times";
    return;
  }

  const [startHour, startMinute] = notificationWindowStart.value.split(":").map(Number);
  const [endHour, endMinute] = notificationWindowEnd.value.split(":").map(Number);
  const start = startHour * 60 + startMinute;
  const end = endHour * 60 + endMinute;
  const duration = (end - start + 24 * 60) % (24 * 60);
  const hours = Math.floor(duration / 60);
  const minutes = duration % 60;
  const parts = [`${hours} hr${hours === 1 ? "" : "s"}`];
  if (minutes > 0) {
    parts.push(`${minutes} min`);
  }
  notificationWindowDuration.textContent = `${parts.join(" ")} daily window`;
}

function setSaving(saving) {
  submitButton.disabled = saving;
  submitLabel.textContent = saving ? "Saving…" : "Save & Update Atlas";
}

async function loadTimezoneOptions(selectedTimezone) {
  const response = await fetch("/notifications/timezones");
  if (!response.ok) {
    throw new Error("timezone options unavailable");
  }

  const payload = await response.json();
  timezoneSelect.replaceChildren();
  for (const timezone of payload.timezones) {
    const option = document.createElement("option");
    option.value = timezone.value;
    option.textContent = timezone.label;
    timezoneSelect.append(option);
  }
  timezoneSelect.value = selectedTimezone;
}

function showProfile(profile) {
  const username = profile.telegram_username ? `@${profile.telegram_username}` : null;
  const displayName = profile.display_name?.trim() || null;
  telegramAccountName.textContent = username || displayName || "Telegram user";
  telegramAccountDetail.textContent = "Verified Telegram account";
  sessionNoteLabel.textContent = "Session secured by Telegram";
}

async function startLogin(requestToken) {
  history.replaceState(null, "", "/notifications");
  showAccessMessage("Verifying your notification settings link…");
  const response = await fetch("/auth/telegram/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_token: requestToken }),
  });
  if (!response.ok) {
    showAccessMessage("This notification settings link is invalid, expired, or already used.");
    return;
  }
  const payload = await response.json();
  window.location.replace(payload.authorization_url);
}

async function loadPreferences() {
  const response = await fetch("/notifications/preferences");
  if (!response.ok) {
    showAccessMessage("Request a new notification settings link from Atlas in Telegram.");
    return;
  }

  const preferences = await response.json();
  const [profileResponse] = await Promise.all([
    fetch("/auth/profile"),
    loadTimezoneOptions(preferences.timezone),
  ]);
  if (!profileResponse.ok) {
    showAccessMessage("Your portal session expired. Request a new link from Atlas.");
    return;
  }
  showProfile(await profileResponse.json());
  notificationWindowStart.value = timeForInput(preferences.notification_window_start);
  notificationWindowEnd.value = timeForInput(preferences.notification_window_end);
  document.querySelector("#notifications-enabled").checked = preferences.notifications_enabled;
  document.querySelector("#notifications-on-weekends").checked =
    preferences.notifications_on_weekends;
  updateNotificationWindowDuration();
  showSettings();
  showSettingsStatus("Signed in through Telegram.");
}

notificationWindowStart.addEventListener("input", updateNotificationWindowDuration);
notificationWindowEnd.addEventListener("input", updateNotificationWindowDuration);
form.addEventListener("input", () => {
  submitLabel.textContent = "Save & Update Atlas";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (Boolean(notificationWindowStart.value) !== Boolean(notificationWindowEnd.value)) {
    showSettingsStatus("Set both notification-window times, or leave both blank.");
    return;
  }

  setSaving(true);
  showSettingsStatus("Saving your notification settings…");
  try {
    const response = await fetch("/notifications/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        timezone: timezoneSelect.value,
        notification_window_start: notificationWindowStart.value || null,
        notification_window_end: notificationWindowEnd.value || null,
        notifications_enabled: document.querySelector("#notifications-enabled").checked,
        notifications_on_weekends: document.querySelector("#notifications-on-weekends").checked,
      }),
    });
    if (response.status === 401) {
      showAccessMessage("Your portal session expired. Request a new link from Atlas.");
      return;
    }
    if (!response.ok) {
      showSettingsStatus("Atlas could not save those notification settings.");
      return;
    }
    submitLabel.textContent = "Settings saved";
    showSettingsStatus("Settings saved.");
  } catch {
    showSettingsStatus("Atlas is temporarily unavailable. Try again shortly.");
  } finally {
    submitButton.disabled = false;
    if (submitLabel.textContent === "Saving…") {
      submitLabel.textContent = "Save & Update Atlas";
    }
  }
});

document.querySelector("#logout").addEventListener("click", async () => {
  await fetch("/auth/session", { method: "DELETE" });
  showAccessMessage("Logged out. Request a new notification settings link when needed.");
});

const requestToken = new URLSearchParams(window.location.hash.slice(1)).get("login");
if (requestToken) {
  startLogin(requestToken).catch(() =>
    showAccessMessage("Telegram login could not be started."),
  );
} else {
  loadPreferences().catch(() =>
    showAccessMessage("Atlas notification settings are temporarily unavailable."),
  );
}
