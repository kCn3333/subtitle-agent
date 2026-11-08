const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');

function renderReport(report) {
  const elements = new Map();
  function element() {
    return {hidden: false, options: [], setAttribute(name,value) {this[name]=value}, removeAttribute(name) {delete this[name]},
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
    assert.equal(ui.get('#current-status').hidden, true);
    assert.equal(ui.get('#reference-field').hidden, false);
    assert.equal(ui.get('#reference-actions').hidden, false);
    assert.equal(ui.get('#reference-question').hidden, true);
    assert.match(ui.get('#reference-summary').textContent, /Wybrana referencja: #4/);
    const options = ui.get('#reference-source').options;
    assert.equal(options[0].value, 'embedded:4');
    assert.equal(options[0].selected, true);
    assert.match(options[0].textContent, /#4 · HDMV_PGS_SUBTITLE · ENG · English/);
    assert.match(options[1].textContent, /#13 · SUBRIP · ENG/);
    assert.match(ui.get('#result-content').innerHTML, /#4 · HDMV_PGS_SUBTITLE · ENG · English/);
  });
}

test('single embedded reference is shown in status without redundant change controls', () => {
  const ui = renderReport({jobType:'PREPARE_WORKPACK', englishRanking:[pgs], selectedEnglish:pgs});
  assert.equal(ui.get('#reference-choice').hidden, false);
  assert.equal(ui.get('#current-status').hidden, true);
  assert.equal(ui.get('#reference-summary').hidden, false);
  assert.match(ui.get('#reference-summary').textContent, /Wybrana referencja: #4/);
  assert.equal(ui.get('#reference-field').hidden, true);
  assert.equal(ui.get('#reference-actions').hidden, true);
});

test('single external reference still requires explicit confirmation', () => {
  const ui = renderReport({jobType:'PREPARE_WORKPACK', externalReferenceConfirmationRequired:true,
    englishRanking:[{sourceType:'external', name:'movie.en.srt', score:70}]});
  assert.equal(ui.get('#reference-choice').hidden, false);
  assert.equal(ui.get('#reference-question').hidden, false);
  assert.equal(ui.get('#reference-summary').hidden, true);
  assert.equal(ui.get('#current-status').hidden, true);
  assert.equal(ui.get('#reference-field').hidden, false);
  assert.equal(ui.get('#reference-actions').hidden, false);
  assert.equal(ui.get('#rebuild').textContent, 'Tak, użyj jako wzorca');
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

for (const pending of [{workpack:{referenceAmbiguous:true}}, {externalReferenceConfirmationRequired:true,workpack:{referenceAmbiguous:false}}]) {
  test(`download waits for reference confirmation: ${JSON.stringify(pending)}`, () => {
    const report={jobType:'PREPARE_WORKPACK',referenceRanking:[pgs,srt],selectedReference:pgs,...pending};
    const ui=renderReport(report);
    assert.equal(ui.get('#download')['aria-disabled'],'true');
    assert.equal(ui.get('#download').href,undefined);
    assert.equal(ui.get('#reference-actions').hidden,false);
    assert.equal(ui.get('#rebuild').textContent,pending.externalReferenceConfirmationRequired?'Tak, użyj jako wzorca':'Zatwierdź wybór');
    const confirmed=renderReport({...report,externalReferenceConfirmationRequired:false,workpack:{referenceAmbiguous:false}});
    assert.equal(confirmed.get('#download')['aria-disabled'],'false');
    assert.equal(confirmed.get('#download').href,'/api/tasks/test/download');
    assert.equal(confirmed.get('#rebuild').textContent,'Zbuduj ponownie');
  });
}

test('weak winning model is presented as no credible hypothesis without a correction offset', () => {
  const ui=renderReport({jobType:'PREPARE_WORKPACK', synchronizationHypotheses:[{
    candidate:'episode.pl.srt',hypothesis:'stałe przesunięcie',offsetMs:-25228,
    confidence:'LOW',sufficientAnchors:false,analysisCoverage:.2,
    verification:'Hipotezy wymagają weryfikacji; nie są gotową synchronizacją.'
  }]});
  const html=ui.get('#result-content').innerHTML;
  assert.match(html,/brak wiarygodnej hipotezy/);
  assert.doesNotMatch(html,/stałe przesunięcie|-25228/);
  assert.match(html,/wymagają weryfikacji/);
});

test('forced reference remains selected and exposes its completeness warning', () => {
  const forced={...pgs,forced:true};
  const ui=renderReport({jobType:'PREPARE_WORKPACK',referenceRanking:[forced],selectedReference:forced});
  assert.match(ui.get('#reference-summary').textContent,/forced=true · kompletność wymaga oceny/);
  assert.match(ui.get('#result-content').innerHTML,/forced=true/);
});
