// Print HTML files to PDF with the pre-installed Chromium.
// node tools/html2pdf.js a.html a.pdf b.html b.pdf ...
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i += 2) {
    await p.goto('file://' + require('path').resolve(args[i]));
    await p.pdf({
      path: args[i + 1], format: 'A4', printBackground: true, displayHeaderFooter: true,
      headerTemplate: '<span></span>',
      footerTemplate: "<div dir='rtl' style='font-size:8pt;width:100%;text-align:center;font-family:DejaVu Sans'>" +
        "עמוד <span class='pageNumber'></span> מתוך <span class='totalPages'></span></div>",
      margin: { top: '14mm', bottom: '16mm', left: '14mm', right: '14mm' },
    });
  }
  await b.close();
})();
