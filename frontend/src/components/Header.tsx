import { Link } from "react-router-dom";

export default function Header() {
  return (
    <div className="bg-white border-b border-neutral-200 font-serif">
      <div className="relative mx-auto pt-9 pb-8 md:px-10">
        <Link
          to="/about"
          className="absolute right-4 sm:right-8 top-4 sm:top-5 text-xs sm:text-base font-medium uppercase tracking-wider text-neutral-500 transition-colors duration-150 hover:text-neutral-700"
        >
          About
        </Link>
        <h1 className="m-0 text-center text-4xl font-serif font-normal leading-tight sm:text-6xl">
          <Link to="/" className="no-underline font-bold">
            Dump Truck News
          </Link>
        </h1>
      </div>
    </div>
  );
}
