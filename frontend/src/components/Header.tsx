import { Link } from "react-router-dom";

export default function Header() {
  return (
    <div className="bg-white border-b border-slate-200">
      <div className="mx-auto max-w-5xl px-5 py-6 md:px-10">
        <h1 className="m-0 text-center text-4xl font-serif font-normal leading-tight text-slate-900 md:text-6xl">
          <Link to="/" className="no-underline text-slate-900">
            Schizo News
          </Link>
        </h1>
      </div>
    </div>
  );
}
