import { Link } from "react-router";
export function NotFound() {
  return <main className="p-6"><h1>Page not found</h1><Link to="/">Back to dashboard</Link></main>;
}
