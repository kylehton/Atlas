const accessMessage = document.querySelector("#access-message");
const accessStatus = document.querySelector("#access-status");
const connectionsContent = document.querySelector("#connections-content");
const connectionMessage = document.querySelector("#connection-message");
const googleAccount = document.querySelector("#google-account");
const googleStatus = document.querySelector("#google-status");
const gmailButton = document.querySelector("#connect-gmail");
const calendarButton = document.querySelector("#connect-calendar");
const disconnectButton = document.querySelector("#disconnect-google");

function showAccessMessage(message) {
  document.body.classList.add("access-only");
  connectionsContent.hidden = true;
  accessStatus.textContent = message;
  accessMessage.hidden = false;
}

function showConnections() {
  document.body.classList.remove("access-only");
  accessMessage.hidden = true;
  connectionsContent.hidden = false;
}

function showProfile(profile) {
  const username = profile.telegram_username ? `@${profile.telegram_username}` : null;
  const displayName = profile.display_name?.trim() || null;
  document.querySelector("#profile-label").textContent =
    `Signed in as ${username || displayName || "Telegram user"}.`;
}

function renderConnection(connection) {
  googleStatus.textContent = connection.status.replaceAll("_", " ");
  googleStatus.dataset.status = connection.status;
  googleAccount.textContent = connection.account_email || "Not connected";
  gmailButton.textContent = connection.gmail_connected ? "Connected" : "Connect";
  calendarButton.textContent = connection.calendar_connected ? "Connected" : "Connect";
  gmailButton.disabled = connection.gmail_connected && connection.status === "connected";
  calendarButton.disabled = connection.calendar_connected && connection.status === "connected";
  disconnectButton.hidden = connection.status === "disconnected";
}

async function loadConnections() {
  const [profileResponse, googleResponse] = await Promise.all([
    fetch("/auth/profile"),
    fetch("/connections/google"),
  ]);
  if (profileResponse.status === 401 || googleResponse.status === 401) {
    showAccessMessage("Request a new portal link from Atlas in Telegram.");
    return;
  }
  if (!profileResponse.ok || !googleResponse.ok) {
    showAccessMessage("Atlas connections are temporarily unavailable.");
    return;
  }
  showProfile(await profileResponse.json());
  renderConnection(await googleResponse.json());
  showConnections();

  const result = new URLSearchParams(window.location.search).get("google");
  if (result === "connected") {
    connectionMessage.textContent = "Google connection updated.";
    history.replaceState(null, "", "/connections");
  } else if (result === "cancelled") {
    connectionMessage.textContent = "Google connection was cancelled.";
    history.replaceState(null, "", "/connections");
  } else if (result === "failed") {
    connectionMessage.textContent = "Google could not complete authorization. Please try again.";
    history.replaceState(null, "", "/connections");
  } else if (result === "changed") {
    connectionMessage.textContent =
      "Your Google connection changed while authorization was open. Please try again.";
    history.replaceState(null, "", "/connections");
  } else if (result === "account-mismatch") {
    connectionMessage.textContent =
      "Disconnect the current Google account before connecting a different one.";
    history.replaceState(null, "", "/connections");
  } else if (result === "account-unavailable") {
    connectionMessage.textContent = "That Google account is already linked to Atlas.";
    history.replaceState(null, "", "/connections");
  }
}

async function connect(capability, button) {
  button.disabled = true;
  connectionMessage.textContent = `Opening Google to authorize ${capability}…`;
  try {
    const response = await fetch("/connections/google/authorize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capability }),
    });
    if (response.status === 401) {
      showAccessMessage("Your portal session expired. Request a new link from Atlas.");
      return;
    }
    if (response.status === 409) {
      connectionMessage.textContent = "Google disconnect is already in progress.";
      button.disabled = false;
      return;
    }
    if (!response.ok) {
      connectionMessage.textContent = "Google connections are not configured yet.";
      button.disabled = false;
      return;
    }
    const payload = await response.json();
    window.location.assign(payload.authorization_url);
  } catch {
    connectionMessage.textContent = "Atlas is temporarily unavailable. Try again shortly.";
    button.disabled = false;
  }
}

gmailButton.addEventListener("click", () => connect("gmail", gmailButton));
calendarButton.addEventListener("click", () => connect("calendar", calendarButton));

disconnectButton.addEventListener("click", async () => {
  if (!window.confirm("Disconnect Google and remove its stored credential from Atlas?")) {
    return;
  }
  disconnectButton.disabled = true;
  connectionMessage.textContent = "Disconnecting Google…";
  try {
    const response = await fetch("/connections/google", { method: "DELETE" });
    if (response.status === 401) {
      showAccessMessage("Your portal session expired. Request a new link from Atlas.");
      return;
    }
    if (!response.ok) {
      connectionMessage.textContent = "Atlas could not disconnect Google.";
      return;
    }
    const result = await response.json();
    renderConnection(result.connection);
    connectionMessage.textContent = result.provider_revoked
      ? "Google disconnected and its stored credential was deleted."
      : "Stored credential deleted. Revoke Atlas in your Google account if it still appears there.";
  } catch {
    connectionMessage.textContent = "Atlas is temporarily unavailable. Try again shortly.";
  } finally {
    disconnectButton.disabled = false;
  }
});

loadConnections().catch(() =>
  showAccessMessage("Atlas connections are temporarily unavailable."),
);
