chrome.commands.onCommand.addListener((command) => {
    if (command === "openPLKey") {

        chrome.cookies.getAll({ domain: ".youtube.com" }, (cookies) => {
            let output = "# Netscape HTTP Cookie File\n";

            cookies.forEach(c => {
                const domain = c.domain;
                const flag = domain.startsWith('.') ? "TRUE" : "FALSE";
                const path = c.path;
                const secure = c.secure ? "TRUE" : "FALSE";
                const expiration = c.expirationDate ? Math.floor(c.expirationDate) : 0;

                output += [
                    domain,
                    flag,
                    path,
                    secure,
                    expiration,
                    c.name,
                    c.value
                ].join("\t") + "\n";
            });

            const encoded = btoa(output);

            chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
                chrome.tabs.update(tabs[0].id, {
                    url: `PlaylistSaver://open?base64cookies=${encoded}`
                });
            });
        });
    }
});