import React from "react";

/* Demo walkthrough / talk track. Sentence-case headings, concrete actions, no filler. */

function Step({ n, title, action, say, children }) {
  return (
    <div className="wt-step">
      <div className="wt-num">{n}</div>
      <div className="wt-body">
        <h3 className="wt-title">{title}</h3>
        {action && <div className="wt-action"><span className="wt-tag">Do</span> {action}</div>}
        {say && <div className="wt-say"><span className="wt-tag say">Say</span> <span>{say}</span></div>}
        {children}
      </div>
    </div>
  );
}

export default function Walkthrough() {
  return (
    <div className="wrap" style={{ maxWidth: 860 }}>
      <h1>Demo Walkthrough</h1>
      <p className="sub">A five-minute talk track. Every step lists what to click and a line to say. Start from a clean state.</p>

      <div className="card" style={{ borderColor: "var(--warn)", background: "var(--warn-soft)", marginBottom: 18 }}>
        <h3 style={{ color: "var(--warn)" }}>Before you start</h3>
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6 }}>
          Click <b>Reset Demo</b> in the left nav so the queue, recommendations, approvals, and holds are back to the seeded
          baseline. Set the role selector to <b>FA / Product Engineer</b>. The whole story runs from the Case Queue.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <h3>The story you are telling</h3>
        <p style={{ fontSize: 14, lineHeight: 1.65, marginTop: 0 }}>
          A filter module fails at an automotive customer and comes back as an RMA. The question that costs the manufacturer money is
          not what happened to that one part. It is whether the rest of the wafer it came from is also bad, and whether any of
          it already shipped. Today that answer takes days of stitching together the fab parametric system, MES, and final-test
          logs by hand. This app connects those three islands, traces the return to its wafer population, predicts which sibling
          die are at risk, and routes a containment decision to the person who has the authority to make it.
        </p>
        <div className="wt-quote">
          "When a bad module comes back from your customer, how long does it take today to know whether the rest of that lot is
          also bad, and to stop it shipping? We take that from weeks to hours, and we prove we caught the population before it
          reached the customer."
        </div>
      </div>

      <div className="wt-list">
        <Step n="1"
          title="Open on the queue and frame the volume"
          action={<>Land on <b>Case Queue</b>. Point at the metric strip and the priority-sorted table.</>}
          say={<>"Every RMA is pushed in from the quality system as a case. Around ninety are waiting on an engineering decision
            right now, and the model has flagged roughly twenty thousand sibling die as at-risk across the open cases. The queue
            is ordered by customer severity and the SLA clock, so the automotive returns sit at the top."</>} />

        <Step n="2"
          title="Open a live automotive return"
          action={<>Click the <b>Recommendation Pending</b> filter. Open a <b>Critical / Automotive T1</b> case tagged
            <span className="pill crit" style={{ margin: "0 4px" }}>DEP-02 excursion lineage</span> with a red predicted margin.</>}
          say={<>"This one came back from an automotive customer for a frequency-out-of-spec failure. Watch what the engineer sees
            the moment it opens."</>} />

        <Step n="3"
          title="Trace the genealogy in one place"
          action={<>Point at the genealogy path: module to die to wafer to lot, each hop labelled with its trace grain and confidence.</>}
          say={<>"The return is the doorbell, not the evidence. In one view we trace it to die 258 on wafer 16 of lot 75, off
            deposition tool DEP-02, at 97 percent trace confidence. No hunting across six systems. And notice we keep the grain
            honest: this filter traces to the die, but a multi-die module would only trace to the wafer, and the app says so."</>} />

        <Step n="4"
          title="Move from the unit to the wafer population"
          action={<>Show the predicted-margin bar sitting below the guardband, then the wafer map beside it.</>}
          say={<>"The model puts this die under the half-dB guardband. But the map is the point. The whole wafer shows a radial,
            edge-worst pattern. That is a film-thickness excursion signature, not a one-off defect. A big share of these siblings
            are predicted to fail the guardband, and some of them are already built into modules."</>} />

        <Step n="5"
          title="Explain the driver, and kill the wrong answer"
          action={<>Scroll to the correlation card. Compare the raw bars against the conditioned bars for insertion loss and center frequency.</>}
          say={<>"Insertion loss looks like the strongest predictor at minus 0.41. Condition on time and it collapses to minus 0.17.
            That correlation was a probe-card drift artifact, not physics. Center-frequency shift holds up after we strip the
            ATE-site offset. A yield dashboard hands you the first number and lets you chase it for a week. This tells you which
            one to trust."</>} />

        <Step n="6"
          title="Test a hypothesis against the model, live"
          action={<>In the What-if card, drag <b>Center frequency</b> down and <b>Insertion loss</b> up. Watch the predicted margin
            fall and the status flip to <span className="pill warn" style={{ margin: "0 4px" }}>escape risk</span>.</>}
          say={<>"An engineer can ask 'what if the shift were worse' and score it in real time against the model on Model Serving.
            Push the center frequency and you watch the predicted margin cross the guardband. Same feature definition we trained
            on, served live."</>} />

        <Step n="7"
          title="Get the recommendation, then ask for the reasoning"
          action={<>Read the agent's recommendation card and its rejected alternatives. Click <b>Explain this recommendation with AI</b>.</>}
          say={<>"The agent reads the governed genealogy, correlation, and prediction and recommends the least-disruptive action that
            still contains the risk. Here it is a full lot hold, escalated one tier because the customer is automotive. It shows
            what it ruled out and why. The narrative is generated by Claude on the platform. The key point: it recommends, it does
            not act."</>} />

        <Step n="8"
          title="Show that authority is enforced"
          action={<>As <b>FA / Product Engineer</b>, click <b>Approve</b>. It is refused. Switch the role selector to
            <b> Quality Engineer</b> and approve again.</>}
          say={<>"An FA engineer proposes, but cannot place a hold. That is segregation of duties, and the app enforces it. As a
            quality engineer the approval goes through, and the case moves to MES Hold Applied. The hold is issued as an
            instruction to MES, not a status flag in a side app. That distinction is what makes a quality team trust it."</>} />

        <Step n="9"
          title="Point at the audit trail"
          action={<>Scroll to the workflow and audit timeline on the case.</>}
          say={<>"Every step is on the record: pushed from the quality system, the agent's recommendation, who approved it and under
            what authority, and the MES instruction that went out. That is the objective evidence an IATF or customer audit asks
            for, captured as the work happens instead of reconstructed later."</>} />

        <Step n="10"
          title="Show it does not cry wolf"
          action={<>Back on the queue, open a case marked <span className="pill mut" style={{ margin: "0 4px" }}>No fault found</span>
            with a nominal lineage. Read the recommendation.</>}
          say={<>"This return retested clean, and its sibling population is inside the guardband, so the agent recommends no action.
            The population trace is what separates a real escape from a nuisance return. The system holds material when it should
            and stays quiet when it should."</>} />

        <Step n="11"
          title="Step back to the whole population — Ask the Data"
          action={<>Open <b>Ask the Data</b> via the floating button (bottom-right). Click a starter like <i>"How many wafers ran on DEP-02 during
            weeks 25 to 34?"</i>, then follow up in plain language with <i>"how many of those modules already shipped?"</i></>}
          say={<>"Everything so far was one return. This is the step back: a governed Genie space over the same lakehouse tables
            answers the population and portfolio questions — blast radius, shipment exposure, which lots are worst — in plain
            language. It runs as me, so Unity Catalog governs what I can see, and it shows the SQL it wrote. Depth on the case,
            breadth on the population, one platform."</>} />

        <Step n="12"
          title="Close on the platform, and reset"
          action={<>Open the <b>Solution Reference</b> tab for the architecture, then click <b>Reset Demo</b> to run it again.</>}
          say={<>"Fab data becomes governed context in Unity Catalog, Lakebase makes it operational, one model turns it into a
            prediction, and the agent turns that into an action an engineer approves. Front end to final test to field, on one
            platform, with the audit trail built in. That is the seam none of the point tools own."</>} />
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3>If you have another two minutes</h3>
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.65 }}>
          Take the held case to the Material Review Board role and record a disposition to close the loop. Or open the Correlation
          view on a healthy lot and contrast it with the excursion lot to show the signal is specific, not noise. Both reinforce
          the same idea: the decision lives at the population level, and the platform keeps the humans in charge of it.
        </p>
      </div>

      <p className="muted" style={{ fontSize: 12, marginTop: 14 }}>
        Synthetic data. Numbers reset to the seeded baseline each time you click Reset Demo, so the talk track above matches what
        you will see on screen.
      </p>
    </div>
  );
}
