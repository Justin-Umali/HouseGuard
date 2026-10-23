# Cloud dashboard: Firebase + Vercel (or GitHub Pages)

Result: a link like `https://house-guard.vercel.app` that works from any phone or PC, with
accounts, a near-live camera preview, danger alerts, and a searchable event history.

```
Raspberry Pi (detects) ◄──► Firebase Firestore ◄──► Website on Vercel (you + family sign in)
```

- The Pi only makes outgoing connections. No port-forwarding.
- Events (with small snapshots), status, and camera preview pictures go through Firestore.
- Email alerts still work exactly as before.

## What the website does

| Feature | How it works |
|---|---|
| Accounts | Anyone can **Create account**. The **owner approves** them on the People tab before they can see anything. |
| Camera preview | A picture about every 2 s **only while someone presses Start preview**. It's not smooth video; it is a free-plan-friendly refresh. Stops after 10 min, when you leave the tab, or when you press Stop. |
| Status band | Plain-language state: All quiet / Person in view / Motion confirmed / Possible intruder / Pi offline / Detection off. |
| Arm / disarm | Owner can turn detection on or off from anywhere. |
| Events | Search, level filter, date filter, snapshot viewer. Owner can delete events. |
| Notifications | Optional browser notification when danger is detected (page must stay open). |
| Full video at home | Optional link to the Pi's local live stream (works on your home Wi-Fi only). |

## Part 1: Firebase (about 10 min)

1. https://console.firebase.google.com -> **Add project** (Analytics off).
2. **Build -> Firestore Database -> Create database** -> *production mode* -> a nearby region.
3. **Build -> Authentication -> Get started -> Email/Password -> Enable -> Save**.
4. **Firestore -> Rules**: paste the contents of `firestore.rules`.
   **Change `owner@example.com` (near the top) to YOUR email**, then **Publish**.
   This email becomes the owner. Everyone else needs your approval.
5. **Project settings (gear) -> Service accounts -> Generate new private key.**
   Save the file as `firebase-key.json` in the `homeguard` folder on the Pi. It is a SECRET.
6. **Project settings -> General -> Your apps -> Web (`</>`) -> Register app** (skip Hosting).
   Copy the values into `docs/firebase-config.js`.

## Part 2: Pi

```bash
cd ~/homeguard
./venv/bin/pip install firebase-admin       # skip if install.sh already did it
nano .env                                   # add the line:  FIREBASE_CRED=firebase-key.json
sudo systemctl restart homeguard            # or: ./venv/bin/python app.py
```
You should see `[cloud] Firebase sync ON`.

## Part 3: Put the website online (Vercel)

1. Push the project to GitHub (the `.gitignore` keeps `.env` and `firebase-key.json` out. Check!).
2. Vercel -> **Add New -> Project** -> import the repo.
3. **Root Directory: `docs`**, **Framework Preset: Other**, leave Build/Output empty. **Deploy.**
   (If you skip Root Directory, Vercel tries to run the Pi's Python app and shows a 500 error.)
4. Firebase -> **Authentication -> Settings -> Authorized domains** -> add your Vercel domain.

GitHub Pages also works: upload the contents of `docs/` and enable Pages.

## Part 4: First sign-in (you become the owner)

1. Open your site -> **Create account** using the **same email you put in `firestore.rules`**.
2. You land on "Waiting for approval". Select **Send verification email**, open the link in your inbox
   (check spam), then select **Check again**.
3. You're in, with the **People** tab. Anyone else who creates an account shows up there for you to **Approve**.

The owner email must be verified; that stops a stranger from claiming your owner address first.

## Updating from the previous version

Replace these, then **re-publish `firestore.rules` in Firebase** (otherwise the new site can't read anything):
- `docs/` (all four files; keep your edited `firebase-config.js`)
- `firestore.rules`
- On the Pi: `homeguard/cloud.py` and `homeguard/engine.py`, then restart.

Accounts you created earlier with "Add user" still work: they appear in People as waiting
(the owner's own account is recognised by email).

## Free-plan limits (Firestore Spark: about 20k writes and 50k reads per day)

- Status: one write per minute (~1,440 a day) plus one per event.
- Preview: one write every 2 s **only while someone watches**, about 30 a minute. A 10-minute session is about 300.
- Old events are not auto-deleted; owners can delete them on the Events tab.

## Security notes

- Camera pictures live in your Firebase project; every approved member can view the preview. Approve only people you trust.
- Remove someone on People -> Remove access. To delete their login entirely, use Firebase -> Authentication.
- Keep `firebase-key.json` and `.env` private. The web config in `firebase-config.js` is not secret.
- Tell household members and guests that cameras are in use.
