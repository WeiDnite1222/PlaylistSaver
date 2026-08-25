function bytesToBase64Url(bytes) {
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 8192) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    }
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function base64UrlToBytes(value) {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/")
        + "=".repeat((4 - value.length % 4) % 4);
    return Uint8Array.from(atob(padded), character => character.charCodeAt(0));
}

async function hmacSha256(keyBytes, data) {
    const key = await crypto.subtle.importKey(
        "raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
    );
    return new Uint8Array(await crypto.subtle.sign("HMAC", key, data));
}

function joinBytes(...arrays) {
    const result = new Uint8Array(arrays.reduce((size, array) => size + array.length, 0));
    let offset = 0;
    for (const array of arrays) {
        result.set(array, offset);
        offset += array.length;
    }
    return result;
}

async function encryptCookies(plaintext, secretKey) {
    const masterKey = base64UrlToBytes(secretKey);
    if (masterKey.length !== 32) {
        throw new Error("The Helper secret key is invalid.");
    }

    const encoder = new TextEncoder();
    const encryptionKey = await hmacSha256(masterKey, encoder.encode("PlaylistSaver cookie encryption"));
    const authenticationKey = await hmacSha256(masterKey, encoder.encode("PlaylistSaver cookie authentication"));
    const nonce = crypto.getRandomValues(new Uint8Array(16));
    const plaintextBytes = encoder.encode(plaintext);
    const ciphertext = new Uint8Array(plaintextBytes.length);

    for (let offset = 0; offset < plaintextBytes.length; offset += 32) {
        const counter = new Uint8Array(4);
        new DataView(counter.buffer).setUint32(0, offset / 32, false);
        const stream = await hmacSha256(encryptionKey, joinBytes(nonce, counter));
        const blockLength = Math.min(32, plaintextBytes.length - offset);
        for (let index = 0; index < blockLength; index++) {
            ciphertext[offset + index] = plaintextBytes[offset + index] ^ stream[index];
        }
    }

    const signedData = joinBytes(Uint8Array.of(1), nonce, ciphertext);
    const tag = await hmacSha256(authenticationKey, signedData);
    return bytesToBase64Url(joinBytes(signedData, tag));
}

async function launchPlaylistSaver() {
    const { secretKey } = await chrome.storage.local.get("secretKey");
    if (!secretKey) {
        await chrome.storage.local.set({ showMissingKeyAlert: true });
        await chrome.runtime.openOptionsPage();
        return;
    }

    const cookies = await chrome.cookies.getAll({ domain: ".youtube.com" });
    let output = "# Netscape HTTP Cookie File\n";
    for (const cookie of cookies) {
        const rawDomain = cookie.domain;
        const domain = cookie.httpOnly ? `#HttpOnly_${rawDomain}` : rawDomain;
        output += [
            domain,
            rawDomain.startsWith(".") ? "TRUE" : "FALSE",
            cookie.path,
            cookie.secure ? "TRUE" : "FALSE",
            cookie.expirationDate ? Math.floor(cookie.expirationDate) : 0,
            cookie.name,
            cookie.value
        ].join("\t") + "\n";
    }

    const encrypted = await encryptCookies(output, secretKey.trim());
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs[0]?.id) {
        throw new Error("No active tab is available to launch PlaylistSaver.");
    }
    await chrome.tabs.update(tabs[0].id, {
        url: `playlistsaver://open?encryptedcookies=${encrypted}`
    });
}

async function openHelperSettings() {
    await chrome.runtime.openOptionsPage();
}

chrome.commands.onCommand.addListener((command) => {
    if (command === "openPLKey") {
        launchPlaylistSaver().catch(error => console.error("Unable to launch PlaylistSaver:", error));
    } else if (command === "openPLSettingPageKey") {
        openHelperSettings().catch(error => console.error("Unable to open Helper settings:", error));
    }
});

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.removeAll(() => {
        chrome.contextMenus.create({
            id: "openPlaylistSaver",
            title: "Open PlaylistSaver",
            contexts: ["page"]
        });
        chrome.contextMenus.create({
            id: "openHelperSettings",
            title: "PlaylistSaver Helper Settings",
            contexts: ["page"]
        });
    });
});

chrome.contextMenus.onClicked.addListener((info) => {
    if (info.menuItemId === "openPlaylistSaver") {
        launchPlaylistSaver().catch(error => console.error("Unable to launch PlaylistSaver:", error));
    } else if (info.menuItemId === "openHelperSettings") {
        openHelperSettings().catch(error => console.error("Unable to open Helper settings:", error));
    }
});
