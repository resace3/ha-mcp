"use strict";
const $ = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="dag-csrf"]')?.content || "";
const roles = ["unknown","exposure","outcome","confounder","mediator","collider","covariate","instrument","selection"];
let dag;

function freshDag() {
  return {schema_version:1,id:`dag-${crypto.randomUUID()}`,title:"Untitled DAG",causal_question:"",exposure_node_id:null,outcome_node_id:null,adjustment_node_ids:[],nodes:[],edges:[],revision:0,status:"draft"};
}
function element(tag, text, attrs={}) {
  const node=document.createElement(tag); if(text!==undefined) node.textContent=text;
  for(const [key,value] of Object.entries(attrs)){if(key==="className")node.className=value;else node.setAttribute(key,value);} return node;
}
function setStatus(message, kind="") { const target=$("status"); target.textContent=message; target.className=`status ${kind}`; }
function syncDocumentFields(){dag.id=$("docId").value.trim();dag.title=$("title").value.trim();dag.causal_question=$("question").value.trim()||null;}
function nodeOptions(selected){const select=element("select"); for(const n of dag.nodes){const option=element("option",`${n.label} (${n.id})`,{value:n.id});option.selected=n.id===selected;select.append(option);}return select;}
function input(value,label){const control=element("input",undefined,{"aria-label":label});control.value=value??"";return control;}

function renderNodes(){
  const root=$("nodes");root.replaceChildren();
  dag.nodes.forEach((node,index)=>{
    const card=element("article",undefined,{className:"row"});
    const id=input(node.id,"Node ID"),label=input(node.label,"Node label"),role=element("select",undefined,{"aria-label":"Node role"});
    for(const name of roles){const option=element("option",name,{value:name});option.selected=node.role===name;role.append(option);}
    const x=input(node.position?.x??100,"Horizontal position"),y=input(node.position?.y??100,"Vertical position");x.type="number";y.type="number";
    const apply=()=>{const old=node.id;node.id=id.value.trim();node.label=label.value.trim();node.role=role.value;node.position={x:Number(x.value),y:Number(y.value)};for(const edge of dag.edges){if(edge.source_node_id===old)edge.source_node_id=node.id;if(edge.target_node_id===old)edge.target_node_id=node.id;}if(dag.exposure_node_id===old)dag.exposure_node_id=node.id;if(dag.outcome_node_id===old)dag.outcome_node_id=node.id;if(role.value==="exposure")dag.exposure_node_id=node.id;if(role.value==="outcome")dag.outcome_node_id=node.id;render();};
    for(const control of [id,label,role,x,y])control.addEventListener("change",apply);
    const remove=element("button","Delete node",{type:"button"});remove.onclick=()=>{if(confirm(`Delete node ${node.label}?`)){dag.nodes.splice(index,1);dag.edges=dag.edges.filter(e=>e.source_node_id!==node.id&&e.target_node_id!==node.id);if(dag.exposure_node_id===node.id)dag.exposure_node_id=null;if(dag.outcome_node_id===node.id)dag.outcome_node_id=null;render();}};
    card.append(element("strong",`Node ${index+1}`),id,label,role,x,y,remove);root.append(card);
  });
}
function renderEdges(){
  const root=$("edges");root.replaceChildren();
  dag.edges.forEach((edge,index)=>{const card=element("article",undefined,{className:"row"}),source=nodeOptions(edge.source_node_id),target=nodeOptions(edge.target_node_id);source.setAttribute("aria-label","Edge source");target.setAttribute("aria-label","Edge target");source.onchange=()=>edge.source_node_id=source.value;target.onchange=()=>edge.target_node_id=target.value;const remove=element("button","Delete edge");remove.onclick=()=>{dag.edges.splice(index,1);renderEdges();};card.append(element("strong",`Edge ${index+1}`),source,element("span","→"),target,remove);root.append(card);});
}
function render(){
  $("docId").value=dag.id;$("docId").disabled=dag.revision>0;$("title").value=dag.title;$("question").value=dag.causal_question||"";$("meta").textContent=`Revision ${dag.revision} · ${dag.status} · ${dag.nodes.length} nodes · ${dag.edges.length} edges`;renderNodes();renderEdges();
}
async function api(path,options={}){
  const headers={...(options.body?{"Content-Type":"application/json","X-DAG-CSRF":csrf}:{}),...(options.headers||{})};const response=await fetch(path,{...options,headers});const result=await response.json();if(!result.ok){const error=new Error(result.error?.message||`Request failed (${response.status})`);error.code=result.error?.code;throw error;}return result.data;
}
async function listDocuments(){const docs=await api("./api/dags");const root=$("documents");root.replaceChildren();for(const doc of docs){const button=element("button",`${doc.title} · r${doc.revision}`);button.onclick=()=>loadDocument(doc.id);root.append(button);}if(!docs.length)root.append(element("p","No saved DAGs.",{className:"muted"}));}
async function loadDocument(id){try{dag=await api(`./api/dags/${encodeURIComponent(id)}`);render();await loadRevisions();setStatus("Document loaded.","success");}catch(error){setStatus(error.message,"error");}}
async function save(){syncDocumentFields();try{dag=await api("./api/dags",{method:dag.revision?"PUT":"POST",body:JSON.stringify(dag)});render();await Promise.all([listDocuments(),loadRevisions()]);setStatus("Saved.","success");}catch(error){setStatus(error.code==="DAG_REVISION_CONFLICT"?"Revision conflict: reload before saving again.":error.message,"error");}}
async function validate(){syncDocumentFields();try{const findings=await api(`./api/dags/${encodeURIComponent(dag.id)}/validate`,{method:"POST",body:JSON.stringify(dag)});setStatus(findings.length?findings.map(f=>`${f.severity.toUpperCase()}: ${f.message}`).join("\n"):"No structural findings. This remains a causal hypothesis.",findings.some(f=>f.severity==="error")?"error":"success");return findings;}catch(error){setStatus(error.message,"error");return null;}}
async function loadRevisions(){const root=$("revisions");root.replaceChildren();if(!dag.revision)return;try{const revisions=await api(`./api/dags/${encodeURIComponent(dag.id)}/revisions`);for(const revision of revisions){const button=element("button",`Restore r${revision}`);button.onclick=()=>restoreRevision(revision);root.append(button);}if(!revisions.length)root.append(element("span","No earlier revisions.",{className:"muted"}));}catch(error){setStatus(error.message,"error");}}
async function restoreRevision(revision){if(!confirm(`Restore revision ${revision}? The current document will be snapshotted.`))return;try{const preview=await api(`./api/dags/${encodeURIComponent(dag.id)}/preview-restore`,{method:"POST",body:JSON.stringify({revision})});dag=await api(`./api/dags/${encodeURIComponent(dag.id)}/restore`,{method:"POST",body:JSON.stringify({revision,expected_revision:dag.revision,confirmation_token:preview.confirmation_token})});render();await loadRevisions();setStatus(`Restored revision ${revision}.`,"success");}catch(error){setStatus(error.message,"error");}}
async function approve(){if(!dag.revision){setStatus("Save the document before approval.","error");return;}try{const preview=await api(`./api/dags/${encodeURIComponent(dag.id)}/preview-approval`,{method:"POST",body:"{}"});if(!confirm(`Approve ${dag.title} revision ${preview.revision}? This confirms only a hypothesis, not proven causality.`))return;dag=await api(`./api/dags/${encodeURIComponent(dag.id)}/approve`,{method:"POST",body:JSON.stringify({revision:preview.revision,confirmation_token:preview.confirmation_token})});render();setStatus("Document approved with explicit confirmation.","success");}catch(error){setStatus(error.message,"error");}}
async function deleteDocument(){if(!dag.revision){dag=freshDag();render();return;}try{const preview=await api(`./api/dags/${encodeURIComponent(dag.id)}/preview-delete`,{method:"POST",body:"{}"});if(!confirm(`Permanently delete ${dag.title} revision ${preview.revision}? A local deletion backup will be retained.`))return;await api(`./api/dags/${encodeURIComponent(dag.id)}/delete`,{method:"POST",body:JSON.stringify({revision:preview.revision,confirmation_token:preview.confirmation_token})});dag=freshDag();render();await listDocuments();$("revisions").replaceChildren();setStatus("Document deleted; a local backup was retained.","success");}catch(error){setStatus(error.message,"error");}}
function download(content,name,type){const url=URL.createObjectURL(new Blob([content],{type}));const anchor=element("a");anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),0);}
async function exportFormat(format){try{syncDocumentFields();if(dag.revision){const data=await api(`./api/dags/${encodeURIComponent(dag.id)}/export?format=${format}`);download(data.content,`${dag.id}.${format==="json"?"json":"dot"}`,format==="json"?"application/json":"text/vnd.graphviz");}else if(format==="json")download(JSON.stringify(dag,null,2),`${dag.id}.json`,"application/json");else setStatus("Save before DOT export.","error");}catch(error){setStatus(error.message,"error");}}

$("new").onclick=()=>{dag=freshDag();render();$("revisions").replaceChildren();setStatus("New unsaved document.");};$("save").onclick=save;$("reload").onclick=()=>dag.revision&&loadDocument(dag.id);$("validate").onclick=validate;$("addNode").onclick=()=>{dag.nodes.push({id:`n${dag.nodes.length+1}`,label:`Node ${dag.nodes.length+1}`,role:"unknown",position:{x:100+dag.nodes.length*30,y:100}});render();};$("addEdge").onclick=()=>{if(dag.nodes.length<2){setStatus("Add at least two nodes first.","error");return;}dag.edges.push({id:`e-${crypto.randomUUID()}`,source_node_id:dag.nodes[0].id,target_node_id:dag.nodes[1].id,relationship:"causes"});renderEdges();};$("refreshRevisions").onclick=loadRevisions;$("approve").onclick=approve;$("delete").onclick=deleteDocument;$("exportJson").onclick=()=>exportFormat("json");$("exportDot").onclick=()=>exportFormat("dot");$("import").onclick=()=>$("importFile").click();$("importFile").onchange=async(event)=>{const file=event.target.files?.[0];if(!file)return;if(file.size>1048576){setStatus("Import rejected: file exceeds 1 MiB.","error");return;}try{const imported=JSON.parse(await file.text());if(!imported||!Array.isArray(imported.nodes)||!Array.isArray(imported.edges))throw new Error("Invalid DAG JSON shape.");const candidate={...imported,revision:0,status:"draft",approved_at:null,approved_by:null};await api("./api/dags/import/validate",{method:"POST",body:JSON.stringify(candidate)});dag=candidate;render();setStatus("Imported as a new schema-validated draft. Review and save it.","success");}catch(error){setStatus(`Import rejected: ${error.message}`,"error");}};
dag=freshDag();render();listDocuments().catch(error=>setStatus(error.message,"error"));
