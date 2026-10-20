# iPhone: Photos → Share → Later

Later accepts one PNG or JPEG per authenticated `POST /api/shortcut-upload` request. Each request creates a separate Inbox submission and returns after saving it, without waiting for AI. Auto-approve in Settings controls whether successful AI results enter the collection automatically.

## Connect and create a token

1. Connect Tailscale on your iPhone using the same account/network as the Later server. Keep the server running.
2. Open your private Later HTTPS address in Safari and sign in.
3. Open **Settings & backup → iPhone Shortcut → Create new token**.
4. Copy the upload URL and token. The token is shown only once. Creating a new token invalidates the old one; **Revoke token** disables Shortcut uploads. This token permits uploads only, never collection reads, deletion, or settings changes. Do not publish a shortcut containing it.

## Build the shortcut once

1. Open **Shortcuts**, tap **+**, and name it **Later**.
2. Open shortcut Details and enable **Show in Share Sheet**. Set the input types to **Images** only. If there is no input, choose **Stop and Respond**.
3. Add **Repeat with Each**, using **Shortcut Input**.
4. Inside the repeat, add **Resize Image**, using **Repeat Item**, width **1600**, height **Auto**. This keeps uploads small for the Pi.
5. Add **Convert Image**, using the resized image; choose **JPEG**. This also handles HEIC photos. If shown, turn off **Preserve Metadata**.
6. Add **URL**, containing the upload URL copied from Later, ending in `/api/shortcut-upload`.
7. Add **Get Contents of URL**, using that URL. Expand its options:
   - Method: **POST**
   - Headers: **Authorization** = `Bearer YOUR_TOKEN` (one space after Bearer; replace YOUR_TOKEN with your token)
   - Headers: **Content-Type** = `image/jpeg`
   - Request Body: **File**
   - File: select the **Converted Image** variable explicitly, not the URL or original image.
8. Add **Get Dictionary Value**, key **ok**, from **Contents of URL**.
9. Add **If** that value is true (or 1). In its success branch add **Show Notification**, text **Sent to Later**. In **Otherwise**, add **Show Alert** containing **Contents of URL**, then **Stop and Respond**. Keep this inside the repeat, so each photo gets its own acknowledgement and errors never display a false success.
10. Save the shortcut.

## Use it

In Photos, select one or more images → Share → Later. Allow network/image access on the first run. If Later is absent, scroll down through the Share Sheet actions or use Edit Actions to add it to favorites.

Each selected image is uploaded separately. A confirmation means it reached the server; AI may still be waiting or processing. With Auto-approve off, review it in Inbox. With Auto-approve on, successful results appear in the collection.

The upload limit is 8 MB and 24 megapixels per converted image, and 60 requests per minute. Failed network uploads are not stored automatically on the phone for later delivery: reconnect and share those images again. Repeating a successful share creates another submission. An invalid/revoked-token error means you need a new token in the shortcut. This workflow requires no browser login on each share and no native iPhone app.

Apple references: [Share Sheet shortcuts](https://support.apple.com/en-lamr/guide/shortcuts/apd163eb9f95/ios), [API requests and file request bodies](https://support.apple.com/guide/shortcuts/apd58d46713f/ios).
