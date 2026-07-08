import Link from "next/link";

export default function Home() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Link href="/league/47/standings" style={{ color: "var(--gold-300)" }}>
        英超排名榜 →
      </Link>
    </main>
  );
}
