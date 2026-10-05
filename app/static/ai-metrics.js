function aiCostText(cost,usage){
  let amount=cost.toLocaleString('pl-PL',{maximumFractionDigits:20});
  if(cost>0&&amount==='0')amount=cost.toString().replace('.',',');
  return `${amount} ${usage?.cost_currency||'(jednostki API)'}`;
}
function hasAiCost(cost){return typeof cost==='number'&&Number.isFinite(cost)&&cost>=0}
function formatAiMetrics(result,includeCost=true){
  const parts=[];
  if(typeof result.elapsed_seconds==='number')parts.push(`Czas żądania: ${result.elapsed_seconds} s`);
  const usage=result.usage;
  if(usage){
    parts.push(`Tokeny: wejście ${usage.prompt_tokens??'—'}, wyjście ${usage.completion_tokens??'—'}, razem ${usage.total_tokens??'—'}`);
    if(includeCost&&hasAiCost(usage.cost))parts.push(`Koszt żądania: ${aiCostText(usage.cost,usage)}`);
  }
  return parts.join(' · ');
}
function formatAiCostSummary(result){
  const cost=result.total_cost??result.usage?.cost;
  return hasAiCost(cost)?`Koszt całej operacji: ${aiCostText(cost,result.usage)}.`:'Koszt całej operacji: API nie podało kosztu.';
}

function formatAiOperationSummary(result,label){
  const ready=Number.isInteger(result.cue_count)?`\nWygenerowano gotowy plik SRT (${result.cue_count} kwestii). Pobierz przyciskiem „Pobierz wynik SRT”.`:'';
  return `PODSUMOWANIE OPERACJI · ${label}\n${formatAiMetrics(result,false)}\n${formatAiCostSummary(result)}${ready}`;
}
