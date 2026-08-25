const keyInput = document.querySelector("#secretKey");
const status = document.querySelector("#status");

chrome.storage.local.get(["secretKey", "showMissingKeyAlert"]).then(async ({ secretKey, showMissingKeyAlert }) => {
    keyInput.value = secretKey || "";

    if (showMissingKeyAlert) {
        await chrome.storage.local.remove("showMissingKeyAlert");
        alert("Set the PlaylistSaver secret key before exporting cookies.");
        keyInput.focus();
    }
});

document.querySelector("#toggleKey").addEventListener("click", event => {
    const showing = keyInput.type === "text";
    keyInput.type = showing ? "password" : "text";
    event.currentTarget.textContent = showing ? "Show" : "Hide";
});

document.querySelector("#saveKey").addEventListener("click", async () => {
    const secretKey = keyInput.value.trim();
    if (!/^[A-Za-z0-9_-]{43}$/.test(secretKey)) {
        status.textContent = "Invalid key. Copy it again from PlaylistSaver.";
        status.style.color = "#ff8989";
        return;
    }

    await chrome.storage.local.set({ secretKey });
    status.textContent = "Secret key saved.";
    status.style.color = "#8bd39a";
});
