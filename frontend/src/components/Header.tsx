import { Link } from "react-router-dom";

export default function Header() {
  return (
    <div className="bg-white border-b border-neutral-200">
      <div className="mx-auto max-w-3xl px-5 py-6 pb-5 md:px-10">
        <h1 className="m-0 text-center text-4xl font-serif font-normal leading-tight md:text-6xl">
          <Link to="/" className="no-underline font-bold">
            Schizo News
          </Link>
        </h1>
        <p className="mt-2 text-center text-base text-neutral-500 font-serif" aria-live="polite">
          Raw news brought to you HOURLY 😱🧠
        </p>
      </div>
    </div>
  );
}
