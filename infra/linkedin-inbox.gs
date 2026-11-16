/**
 * LinkedIn comment reader for the team. It runs in Abhi's own Google account and answers one question: which LinkedIn
 * "commented on your post" emails arrived in the last 10 days. pipeline/comments.py asks it every 10 minutes.
 *
 * It returns only emails from linkedin.com whose subject says someone commented, replied or mentioned him. It reads
 * nothing else, changes nothing in Gmail and sends no email. It answers only a POST carrying KEY.
 *
 * Setup, once (see the pull request for the same steps):
 *   1. script.google.com > New project. Replace everything with this file. Put the key from the setup note in KEY.
 *   2. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone. Deploy, then allow access.
 *   3. Copy the Web app URL into the repository secret LINKEDIN_INBOX_URL, and the same key into LINKEDIN_INBOX_KEY.
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
  GmailApp.search(QUERY, 0, 25).forEach(function (thread) {
    thread.getMessages().forEach(function (m) {
      if (!/linkedin\.com/i.test(m.getFrom())) return;
      rows.push({
        id: m.getId(),
        date: m.getDate().toISOString(),
        subject: m.getSubject(),
        body: m.getPlainBody().slice(0, 6000)
      });
    });
  });
  return reply({ ok: true, messages: rows.slice(-40) });
}

function doGet() {
  return reply({ ok: false });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
