import React from "react";

export default function EvidenceLibrary({ scenario, analysisOutput }) {
  const selectedProcedures = analysisOutput?.selected_procedure_ids || ["PROC-001"];

  const ecssLibrary = [
    {
      procedure_id: "PROC-001",
      title: "ADCS Gyro SEU Register Recovery Procedure",
      document: "ECSS-E-ST-70-11C",
      source: "ESA / ECSS STANDARD PROCEDURE LIBRARY",
      version: "1.0.0",
      section: "Section 5.4.2 (Gyro Recovery)",
      clause: "Clause 5.4.2.1 - SEU Bitflip Register Reset",
      relevance: 0.95,
      summary: "Isolate affected rate sensor register, execute software reset, verify rate convergence, and resume nominal 3-axis attitude determination.",
    },
    {
      procedure_id: "PROC-002",
      title: "EPS Solar Array Bus Undervolt Shedding Procedure",
      document: "ECSS-E-ST-70-11C",
      source: "ESA / ECSS STANDARD PROCEDURE LIBRARY",
      version: "1.0.0",
      section: "Section 6.2.1 (Power Load Shedding)",
      clause: "Clause 6.2.1.4 - Emergency Load Shedding Floor",
      relevance: 0.88,
      summary: "Shed non-essential payloads, switch solar array drive electronics to backup channel, and hold emergency battery charge reserve above 15%.",
    },
    {
      procedure_id: "PROC-003",
      title: "OBC Watchdog Counter Recovery Procedure",
      document: "ECSS-E-ST-70-11C",
      source: "ESA / ECSS STANDARD PROCEDURE LIBRARY",
      version: "1.0.0",
      section: "Section 7.1.3 (On-Board Software FDIR)",
      clause: "Clause 7.1.3.2 - Controlled Flight Software Reboot",
      relevance: 0.82,
      summary: "Clear software task deadlock, log stack trace to non-volatile memory, execute controlled warm reboot, and verify watchdog heartbeat.",
    },
    {
      procedure_id: "PROC-004",
      title: "TCS Thermal Runaway Heater Disable Procedure",
      document: "ECSS-E-ST-70-11C",
      source: "ESA / ECSS STANDARD PROCEDURE LIBRARY",
      version: "1.0.0",
      section: "Section 8.3.1 (Thermal Control FDIR)",
      clause: "Clause 8.3.1.9 - Thermal Survival Cutoff",
      relevance: 0.91,
      summary: "Immediately disable stuck battery heater zone relay, monitor temperature slope below 85°C survival ceiling, and maintain passive radiator aspect.",
    },
  ];

  return (
    <section className="ops-view-container" aria-labelledby="evidence-heading">
      <div className="view-title-bar">
        <h1 id="evidence-heading" className="view-title">
          ECSS PROCEDURE LIBRARY &amp; RAG EVIDENCE CITATIONS
        </h1>
        <div className="view-actions">
          <span className="info-chip">RAG DATABASE: ECSS-E-ST-70-11C</span>
          <span className="info-chip">INDEXED PROCEDURES: {ecssLibrary.length}</span>
        </div>
      </div>

      {/* ECSS Procedure Library Table */}
      <div className="section-block">
        <h2 className="section-title">RETRIEVED ECSS STANDARD RECOVERY PROCEDURES</h2>
        <table className="ops-table" aria-label="ECSS recovery procedures library">
          <thead>
            <tr>
              <th>PROCEDURE ID</th>
              <th>TITLE</th>
              <th>DOCUMENT &amp; SOURCE</th>
              <th>VERSION</th>
              <th>SECTION &amp; CLAUSE</th>
              <th>RELEVANCE SCORE</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {ecssLibrary.map((item) => {
              const isSelected = selectedProcedures.includes(item.procedure_id);
              return (
                <tr key={item.procedure_id} className={isSelected ? "row-highlight" : ""}>
                  <td className="mono bold">{item.procedure_id}</td>
                  <td className="bold">{item.title}</td>
                  <td className="mono fs-xs">{item.document} ({item.source})</td>
                  <td className="mono">{item.version}</td>
                  <td className="mono fs-xs">{item.section} - {item.clause}</td>
                  <td className="mono bold green-text">{(item.relevance * 100).toFixed(0)}%</td>
                  <td>
                    <span className={`badge-pill badge-${isSelected ? "nominal" : "gray"}`}>
                      [{isSelected ? "RETRIEVED & CITED" : "LIBRARY ITEM"}]
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Citation Cards */}
      <div className="section-block">
        <h2 className="section-title">EVIDENCE CITATION CLAUSE DETAILS</h2>
        <div className="ops-grid grid-2">
          {ecssLibrary.map((item) => (
            <div key={item.procedure_id} className="ops-card">
              <div className="card-header flex-between">
                <span className="mono bold">{item.procedure_id}: {item.document}</span>
                <span className="mono fs-xs">RELEVANCE: {(item.relevance * 100).toFixed(0)}%</span>
              </div>
              <div className="card-body">
                <h4 className="bold mb-5">{item.title}</h4>
                <div className="ops-kv">
                  <span className="kv-key">CLAUSE:</span>
                  <span className="kv-val mono fs-xs">{item.clause}</span>
                </div>
                <div className="ops-kv">
                  <span className="kv-key">PROCEDURE SUMMARY:</span>
                  <span className="kv-val">{item.summary}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
