let activeRnaSecondaryJobId = null;
let rnaSecondaryPolling = false;
async function submitRnaSecondaryStructure() {
    const input=document.getElementById("rnaSecondarySequence"), status=document.getElementById("rnaSecondaryStatus");
    const preview=document.getElementById("rnaSecondaryPreview"), submit=document.getElementById("rnaSecondarySubmit");
    const download=document.getElementById("rnaSecondaryDownload");
    if(!input||!status||!submit||!download)return;
    const sequence=(input.value||"").replace(/\s+/g,"").toUpperCase();
    if(sequence.length<4||sequence.length>10000||/[^ACGUT]/.test(sequence)){status.textContent="Séquence invalide : 4 à 10 000 nucléotides, uniquement A/C/G/U/T.";return;}
    if(!window.api||!window.api.isAuthenticated()){status.textContent="Connecte-toi avant de lancer une prédiction.";return;}
    submit.disabled=true;download.disabled=true;preview.textContent="";status.textContent="Soumission de RNA-FM…";
    try{const result=await window.api.submitRnaSecondaryStructure(sequence);activeRnaSecondaryJobId=result.job_id;status.textContent="Job "+result.job_id+" en file d’attente…";await pollRnaSecondaryStructure(result.job_id);}
    catch(error){status.textContent=error.message||"La soumission a échoué.";}finally{submit.disabled=false;}
}
async function pollRnaSecondaryStructure(jobId){
    if(rnaSecondaryPolling)return;rnaSecondaryPolling=true;
    const status=document.getElementById("rnaSecondaryStatus"),preview=document.getElementById("rnaSecondaryPreview"),download=document.getElementById("rnaSecondaryDownload");
    const started=Date.now();
    try{
        while(Date.now()-started<12*60*1000){
            const job=await window.api.getRnaSecondaryStructureJob(jobId);
            status.textContent="Job "+jobId+" — "+job.status+" ("+(job.progress||0)+"%)";
            if(job.status==="completed"){
                const metadata=job.metadata||{};
                preview.textContent=metadata.preview||("Artefact : "+(metadata.artifact_filename||"RNA-FM")+"\nFormat : "+(metadata.artifact_format||"inconnu")+"\nMéthode : "+(metadata.method||"RNA-FM")+"\n"+(metadata.interpretation_scope||""));
                download.disabled=!job.artifact_available;status.textContent="Prédiction terminée. Résultat informatique, non expérimental.";return;
            }
            if(job.status==="failed"){status.textContent=job.error||"La prédiction a échoué.";return;}
            await new Promise(resolve=>setTimeout(resolve,3000));
        }
        status.textContent="Délai de l’interface dépassé; le job peut continuer côté serveur.";
    }catch(error){status.textContent=error.message||"Impossible de récupérer le statut du job.";}finally{rnaSecondaryPolling=false;}
}
async function downloadRnaSecondaryArtifact(){
    if(!activeRnaSecondaryJobId||!window.api)return;
    try{await window.api.downloadRnaSecondaryArtifact(activeRnaSecondaryJobId);}
    catch(error){const status=document.getElementById("rnaSecondaryStatus");if(status)status.textContent=error.message||"Téléchargement refusé.";}
}