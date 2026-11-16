/**
 * LinkedIn comment reader for the team. It runs in Abhi's own Google account and answers one question: which LinkedIn
 * "commented on your post" emails arrived in the last 10 days. pipeline/comments.py asks it every 10 minutes.
 *
 * Read-only: it uses the Gmail API (advanced Gmail service) with only the gmail.readonly scope, set in the manifest
 * (infra/linkedin-inbox.appsscript.json). It returns only emails from linkedin.com whose subject says someone commented,
 * replied or mentioned him. It changes nothing in Gmail and sends no email. It answers only a POST carrying KEY.
 *
 * Setup, once:
 *   1. script.google.com > New project. Replace Code.gs with this file and put the key in KEY (only on the KEY line;
 *      the check in doPost must keep 'PASTE_THE_KEY_HERE').
 *   2. Project Settings > tick "Show appsscript.json manifest file in editor", then replace appsscript.json with
 *      infra/linkedin-inbox.appsscript.json. That turns on the Gmail service with read-only access.
 *   3. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone. Then Run doPost once in the editor and
 *      allow access: Google should ask only to "View your email messages and settings".
 *   4. Copy the Web app URL into the repository secret LINKEDIN_INBOX_URL, and the same key into LINKEDIN_INBOX_KEY.
 */
const KEY = 'PASTE_THE_KEY_HERE';
const QUERY = 'from:linkedin.com newer_than:10d (subject:commented OR subject:replied OR subject:"mentioned you")';

function doPost(e) {
  let asked = {};
  try {
    asked = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    asked = {};
  }
  if (KEY === 'PASTE_THE_KEY_HERE' || asked.key !== KEY) {
    return reply({ ok: false });
  }
  const rows = [];
  const found = Gmail.Users.Messages.list('me', { q: QUERY, maxResults: 40 });
  (found.messages || []).forEach(function (ref) {
    const m = Gmail.Users.Messages.get('me', ref.id, { format: 'full' });
    const head = {};
    ((m.payload && m.payload.headers) || []).forEach(function (h) { head[h.name.toLowerCase()] = h.value; });
    if (!/linkedin\.com/i.test(head.from || '')) return;
    rows.push({
      id: m.id,
      date: new Date(Number(m.internalDate)).toISOString(),
      subject: head.subject || '',
      body: plainText(m.payload).slice(0, 6000)
    });
  });
  rows.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
  return reply({ ok: true, messages: rows.slice(-40) });
}

function plainText(part) {
  if (!part) return '';
  if (part.mimeType === 'text/plain' && part.body && part.body.data) {
    return Utilities.newBlob(Utilities.base64DecodeWebSafe(part.body.data)).getDataAsString();
  }
  for (let i = 0; i < (part.parts || []).length; i++) {
    const text = plainText(part.parts[i]);
    if (text) return text;
  }
  return '';
}

function doGet() {
  return reply({ ok: false });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
