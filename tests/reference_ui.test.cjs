const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');

function renderReport(report) {
  const elements = new Map();
  function element() {
    return {hidden: false, options: [], setAttribute() {}, removeAttribute() {},
      replaceChildren() {this.options = []}, append(...options) {this.options.push(...options)},
      get outerHTML() {return String(this.textContent ?? '')}};
  }
  const document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
    createElement: element,
  };
  const script = readFileSync('app/static/app.js', 'utf8').split('async function load(')[0];
  const context={document,report};
  runInNewContext(readFileSync('app/static/media-title.js','utf8'),context);
  runInNewContext(script + '\nrender({jobId:"test",report});', context);
  return elements;
}

const pgs = {sourceType:'embedded', streamIndex:4, codec:'hdmv_pgs_subtitle', language:'eng', title:'English', score:90};
const srt = {sourceType:'embedded', streamIndex:13, codec:'subrip', language:'eng', score:70};

for (const workpack of [null, {referenceAmbiguous:false}]) {
  test(`multiple embedded sources can be chosen with ${workpack ? 'unambiguous pack' : 'no pack'}`, () => {
    const ui = renderReport({jobType:'PREPARE_WORKPACK', englishRanking:[pgs,srt], selectedEnglish:pgs, workpack});
    assert.equal(ui.get('#reference-choice').hidden, false);
    const options = ui.get('#reference-source').options;
    assert.equal(options[0].value, 'embedded:4');
    assert.equal(options[0].selected, true);
    assert.match(options[0].textContent, /#4 · HDMV_PGS_SUBTITLE · ENG · English/);
    assert.match(options[1].textContent, /#13 · SUBRIP · ENG/);
    assert.match(ui.get('#result-content').innerHTML, /#4 · HDMV_PGS_SUBTITLE · ENG · English/);
  });
}

test('single embedded reference needs no selector', () => {
  const ui = renderReport({jobType:'PREPARE_WORKPACK', englishRanking:[pgs], selectedEnglish:pgs});
  assert.equal(ui.get('#reference-choice').hidden, true);
});

test('single external reference still requires explicit confirmation', () => {
  const ui = renderReport({jobType:'PREPARE_WORKPACK', externalReferenceConfirmationRequired:true,
    englishRanking:[{sourceType:'external', name:'movie.en.srt', score:70}]});
  assert.equal(ui.get('#reference-choice').hidden, false);
  assert.equal(ui.get('#reference-question').hidden, false);
  assert.equal(ui.get('#reference-source').options[0].value, 'external:movie.en.srt');
});

test('French reference is selectable alongside English and shown with its language', () => {
  const french = {...srt, streamIndex: 7, language:'fra', title:'French'};
  const ui = renderReport({jobType:'PREPARE_WORKPACK', referenceRanking:[pgs,french],
    englishRanking:[pgs], selectedReference:french, workpack:{referenceAmbiguous:false}});
  const options = ui.get('#reference-source').options;
  assert.equal(options.length, 2);
  assert.equal(options[1].value, 'embedded:7');
  assert.equal(options[1].selected, true);
  assert.match(options[1].textContent, /FRA · French/);
  assert.match(ui.get('#result-content').innerHTML, /Wybrana referencja/);
});
