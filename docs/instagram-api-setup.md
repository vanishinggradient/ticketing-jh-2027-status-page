# Instagram API setup (for the @japanhabba admin)

The status page can log each @japanhabba post into the team sheet automatically, so registration jumps line up with the posts that caused them.

It uses Meta's official **Instagram API with Instagram Login**. No scraping, no shared passwords.


## What you need

| Requirement | Why |
|---|---|
| Admin access to @japanhabba | Only the account owner can grant access |
| A Professional account (Business or Creator) | The API only works with these. Switching is free and reversible |
| About 15 minutes | One-time setup |


## Steps

1. In the Instagram app: Settings, then Account type and tools, then switch to a **Professional account** (Business or Creator).

2. Go to [developers.facebook.com](https://developers.facebook.com/apps), create an app, and pick type **Business**.

3. In the app, add the product **Instagram API with Instagram Login**.

4. Under API setup, add @japanhabba as an **Instagram tester** and accept the invite in the Instagram app.

5. Click **Generate token** next to the account. Development mode is enough for our own account, so no App Review is needed.

6. Store the token as a repo secret. Don't paste it anywhere else:

   ```
   gh secret set IG_ACCESS_TOKEN -R vanishinggradient/ticketing-jh-2027-status-page
   ```

7. Tell the ticketing lead it's done.


## Good to know

> The token expires after 60 days. A refresh is needed before then, or the post log stops.

- The only permission used is `instagram_business_basic`: read the account's own posts.
- Nothing is ever posted or changed on Instagram.

Reference: [Meta, Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started).
