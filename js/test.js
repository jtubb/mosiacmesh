var interval = 200; // ms
var expected = Date.now() + interval;

var drift_history = [];
var drift_history_samples = 10;
var drift_correction = 0;

function calc_drift(arr){
  // Calculate drift correction.

  /*
  In this example I've used a simple median.
  You can use other methods, but it's important not to use an average. 
  If the user switches tabs and back, an average would put far too much
  weight on the outlier.
  */

  var values = arr.concat(); // copy array so it isn't mutated
  
  values.sort(function(a,b){
    return a-b;
  });
  if(values.length ===0) return 0;
  var half = Math.floor(values.length / 2);
  if (values.length % 2) return values[half];
  var median = (values[half - 1] + values[half]) / 2.0;
  
  return median;
}

setTimeout(step, interval);
function step() {
  var dt = Date.now() - expected; // the drift (positive for overshooting)
  if (dt > interval) {
    // something really bad happened. Maybe the browser (tab) was inactive?
    // possibly special handling to avoid futile "catch up" run
  }
  // do what is to be done
       
  // don't update the history for exceptionally large values
  if (dt <= interval) {
    // sample drift amount to history after removing current correction
    // (add to remove because the correction is applied by subtraction)
      drift_history.push(dt + drift_correction);

    // predict new drift correction
    drift_correction = calc_drift(drift_history);

    // cap and refresh samples
    if (drift_history.length >= drift_history_samples) {
      drift_history.shift();
    }    
  }
   
  expected += interval;
  // take into account drift with prediction
  setTimeout(step, Math.max(0, interval - dt - drift_correction));
}