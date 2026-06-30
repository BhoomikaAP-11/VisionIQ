export default function InsightsPanel({ insights = [], recommendations = [] }) {
  return (
    <div className="row even">
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Insights</h3>
        {insights.length === 0 ? (
          <div className="muted">No insights generated.</div>
        ) : (
          <ul className="insight-list">
            {insights.map((i, k) => <li key={k}>{i}</li>)}
          </ul>
        )}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Recommendations</h3>
        {recommendations.length === 0 ? (
          <div className="muted">No recommendations generated.</div>
        ) : (
          <ul className="insight-list">
            {recommendations.map((r, k) => <li key={k}>{r}</li>)}
          </ul>
        )}
      </div>
    </div>
  )
}
