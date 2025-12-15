import { useMemo } from "react";
import { Link } from "react-router-dom";

export default function About() {
  const creditLine = useMemo(() => {
    const names = ["Addison Goolsbee", "Koray Akduman"];
    const shuffled = [...names].sort(() => Math.random() - 0.5);
    return shuffled.map((name) => {
      if (name === "Addison Goolsbee") {
        return (
          <a
            key={name}
            href="https://addisongoolsbee.com"
            target="_blank"
            rel="noreferrer"
            className="font-semibold text-neutral-500 underline"
          >
            {name}
          </a>
        );
      }
      return (
        <a
          key={name}
          href="https://korayakduman.com"
          target="_blank"
          rel="noreferrer"
          className="font-semibold text-neutral-500 underline"
        >
          {name}
        </a>
      );
    });
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-5 py-2 md:py-12 font-serif md:px-10">
      <p className="mt-4">💩 🚛 🗞️</p>
      <p className="mt-4">Welcome to Dump Truck News. </p>
      <p className="mt-4">
        This site is run by two professional waste collectors who are deeply committed to providing timely and accurate
        news updates. Our team works around the clock to ensure that our readers receive the latest information on waste
        management, recycling initiatives, and environmental policies.
      </p>
      <p className="mt-4">
        We believe that staying informed about waste-related issues is crucial for fostering a cleaner and more
        sustainable future. Our articles cover a wide range of topics, from innovative waste reduction techniques to
        community recycling programs.
      </p>
      <p className="mt-4">
        Thank you for visiting Dump Truck News. We hope you find our content informative and engaging. If you have any
        questions or suggestions, please feel free to reach out to us.
      </p>
      <p className="mt-4">
        Made with ambivalence by {creditLine[0]} <span className="px-1">&</span> {creditLine[1]}
      </p>
      <p className="mt-4 text-base text-neutral-500">
        <Link to="/" className="font-semibold underline">
          Go back to news feed
        </Link>
      </p>
    </main>
  );
}
