import { useState, useEffect, useRef } from 'react';
import Head from 'next/head';
import dynamic from 'next/dynamic';
import {
  Sparkles,
  Target,
  ArrowRight,
  ExternalLink,
  Check,
  X,
  Copy,
  BarChart3,
  Cpu,
  ShieldCheck,
  Activity,
  AlertCircle,
  Lightbulb,
  DollarSign,
  TrendingUp,
  UserCheck,
  TrendingDown,
  Layers,
  ChevronLeft,
  ChevronRight,
  Shield,
  Eye,
  EyeOff
} from 'lucide-react';

// Dynamically import Recharts to prevent SSR (Server-Side Rendering) issues
const ResponsiveContainer = dynamic(
  () => import('recharts').then((mod) => mod.ResponsiveContainer),
  { ssr: false }
);
const RadarChart = dynamic(
  () => import('recharts').then((mod) => mod.RadarChart),
  { ssr: false }
);
const PolarGrid = dynamic(
  () => import('recharts').then((mod) => mod.PolarGrid),
  { ssr: false }
);
const PolarAngleAxis = dynamic(
  () => import('recharts').then((mod) => mod.PolarAngleAxis),
  { ssr: false }
);
const PolarRadiusAxis = dynamic(
  () => import('recharts').then((mod) => mod.PolarRadiusAxis),
  { ssr: false }
);
const Radar = dynamic(
  () => import('recharts').then((mod) => mod.Radar),
  { ssr: false }
);
const Legend = dynamic(
  () => import('recharts').then((mod) => mod.Legend),
  { ssr: false }
);
const Tooltip = dynamic(
  () => import('recharts').then((mod) => mod.Tooltip),
  { ssr: false }
);

// Helper to extract clean domain name from URL
const getCleanDomain = (url) => {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace('www.', '');
  } catch (e) {
    return url;
  }
};

// SVG Animated Circular Progress Ring Component
const CircularProgressRing = ({ score, size = 80, strokeWidth = 8 }) => {
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = circumference - (score / 100) * circumference;

  let strokeColor = 'stroke-red-500';
  if (score > 40 && score <= 70) {
    strokeColor = 'stroke-amber-500';
  } else if (score > 70) {
    strokeColor = 'stroke-emerald-500';
  }

  return (
    <div className="relative flex items-center justify-center">
      <svg width={size} height={size} className="transform -rotate-90">
        <circle
          className="stroke-neutral-800"
          fill="transparent"
          strokeWidth={strokeWidth}
          r={radius}
          cx={size / 2}
          cy={size / 2}
        />
        <circle
          className={`${strokeColor} transition-all duration-1000 ease-out`}
          fill="transparent"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          r={radius}
          cx={size / 2}
          cy={size / 2}
        />
      </svg>
      <span className="absolute text-lg font-bold text-white">{score}</span>
    </div>
  );
};

// Evidence field label mapping
const EVIDENCE_LABELS = {
  name: 'Name',
  tagline: 'Tagline',
  core_features: 'Core Features',
  target_market: 'Target Market',
  pricing_model: 'Pricing Model',
  strengths: 'Strengths',
  weaknesses: 'Weaknesses',
};

export default function Home() {
  const [idea, setIdea] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const [copied, setCopied] = useState(false);
  const [expandedEvidence, setExpandedEvidence] = useState(new Set());

  // Ref for competitor scroll container
  const scrollRef = useRef(null);

  const steps = [
    "Parsing your idea...",
    "Searching for competitors...",
    "Classifying sources...",
    "Analyzing competitor products...",
    "Generating strategy..."
  ];

  // Set up loading animations steps
  useEffect(() => {
    let timers = [];
    if (loading) {
      setLoadingStep(0);
      timers.push(setTimeout(() => setLoadingStep(1), 3000));
      timers.push(setTimeout(() => setLoadingStep(2), 6000));
      timers.push(setTimeout(() => setLoadingStep(3), 12000));
      timers.push(setTimeout(() => setLoadingStep(4), 18000));
    }
    return () => timers.forEach(clearTimeout);
  }, [loading]);

  const handleAnalyze = async (e) => {
    e.preventDefault();
    if (!idea.trim()) return;

    setLoading(true);
    setError(null);
    setData(null);
    setExpandedEvidence(new Set());

    try {
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const response = await fetch(`${backendUrl}/api/analyze`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ idea }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'An error occurred while analyzing your idea.');
      }

      const result = await response.json();
      setData(result);
    } catch (err) {
      console.error(err);
      setError(err.message || 'Failed to connect to the backend server. Make sure it is running.');
    } finally {
      setLoading(false);
    }
  };

  const toggleEvidence = (idx) => {
    setExpandedEvidence((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  };

  const scrollCompetitors = (direction) => {
    if (!scrollRef.current) return;
    const cardWidth = 340 + 24; // card width + gap
    scrollRef.current.scrollBy({
      left: direction === 'right' ? cardWidth : -cardWidth,
      behavior: 'smooth',
    });
  };

  const handleCopyReport = () => {
    if (!data) return;

    const reportText = `IDEALENS STARTUP VALIDATION & COMPETITIVE INTELLIGENCE REPORT
=============================================================

1. IDEA SUMMARY
---------------
Domain: ${data.parsed_idea.domain}
Problem: ${data.parsed_idea.problem}
Solution: ${data.parsed_idea.solution}
Target Users: ${data.parsed_idea.target_users}
Unique Angle: ${data.parsed_idea.unique_angle}

Key Features:
${data.parsed_idea.key_features.map((f, i) => `${i + 1}. ${f}`).join('\n')}

2. EXECUTIVE SUMMARY
--------------------
${data.strategy.summary}

3. COMPETITIVE LANDSCAPE
------------------------
${data.competitors
  .map(
    (c, i) => `Competitor ${i + 1}: ${c.name}
- Tagline: ${c.tagline}
- Website: ${c.url}
- Pricing Model: ${c.pricing_model}
- Core Features: ${c.core_features.join(', ')}
- Strengths: ${c.strengths.join(', ')}
- Weaknesses: ${c.weaknesses.join(', ')}`
  )
  .join('\n\n')}

4. STRATEGY SCORES
------------------
- Innovation: ${data.strategy.idea_scores.innovation}/100
- Market Gap: ${data.strategy.idea_scores.market_gap}/100
- Feasibility: ${data.strategy.idea_scores.feasibility}/100
- Competition Density: ${data.strategy.idea_scores.competition_density}/100

5. DIFFERENTIATION SUGGESTIONS
------------------------------
${data.strategy.suggestions
  .map((s) => `[${s.priority} Priority] ${s.title}: ${s.description}`)
  .join('\n')}`;

    navigator.clipboard.writeText(reportText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Prepare Chart Data
  const getChartData = () => {
    if (!data) return [];
    
    // Axes: Innovation, Market Gap, Feasibility, Competition, Overall
    // For competition, we invert "competition_density" so that a higher score means "Lighter competition / better position"
    const calculateOverall = (scores) => {
      const compVal = 100 - scores.competition_density;
      return Math.round((scores.innovation + scores.market_gap + scores.feasibility + compVal) / 4);
    };

    const ideaScores = data.strategy.idea_scores;
    const ideaOverall = calculateOverall(ideaScores);

    const metrics = [
      { subject: 'Innovation', label: 'Innovation' },
      { subject: 'Market Gap', label: 'Market Gap' },
      { subject: 'Feasibility', label: 'Feasibility' },
      { subject: 'Competition', label: 'Competition Strength (Inverse Density)' },
      { subject: 'Overall', label: 'Overall Positioning' }
    ];

    return metrics.map(m => {
      const item = { subject: m.subject };
      
      // Plot user idea
      if (m.subject === 'Innovation') item['Your Idea'] = ideaScores.innovation;
      else if (m.subject === 'Market Gap') item['Your Idea'] = ideaScores.market_gap;
      else if (m.subject === 'Feasibility') item['Your Idea'] = ideaScores.feasibility;
      else if (m.subject === 'Competition') item['Your Idea'] = 100 - ideaScores.competition_density;
      else if (m.subject === 'Overall') item['Your Idea'] = ideaOverall;

      // Plot each competitor
      data.strategy.competitor_scores.forEach(comp => {
        const compScores = comp.scores;
        const compOverall = calculateOverall(compScores);

        if (m.subject === 'Innovation') item[comp.name] = compScores.innovation;
        else if (m.subject === 'Market Gap') item[comp.name] = compScores.market_gap;
        else if (m.subject === 'Feasibility') item[comp.name] = compScores.feasibility;
        else if (m.subject === 'Competition') item[comp.name] = 100 - compScores.competition_density;
        else if (m.subject === 'Overall') item[comp.name] = compOverall;
      });

      return item;
    });
  };

  const chartData = getChartData();
  const competitorColors = ['#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4', '#f97316', '#84cc16', '#e11d48'];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-neutral-200 antialiased selection:bg-blue-500 selection:text-white">
      <Head>
        <title>IdeaLens — AI Startup Validation & Competitive Intelligence</title>
        <meta name="description" content="Validate your startup idea using an autonomous multi-agent AI pipeline and view a rich competitive intelligence dashboard." />
        <link rel="icon" href="/favicon.ico" />
      </Head>

      {/* Navigation Header */}
      <header className="border-b border-[#222222] bg-[#0c0c0c] py-4 px-6 sticky top-0 z-40 backdrop-blur-md bg-opacity-80">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="bg-gradient-to-tr from-blue-600 to-indigo-600 p-2 rounded-xl text-white shadow-lg shadow-blue-500/20">
              <Layers className="h-6 w-6" />
            </div>
            <div>
              <span className="text-xl font-bold tracking-tight text-white">Idea<span className="text-blue-500">Lens</span></span>
              <span className="hidden sm:inline-block ml-2 text-xs uppercase px-2 py-0.5 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 font-semibold tracking-wider">Agentic Pipeline</span>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* SECTION 1: HERO INPUT */}
        <section className="text-center mb-16 fade-in">
          <h1 className="text-4xl sm:text-6xl font-extrabold text-white tracking-tight leading-none mb-6">
            Map the Market Before <br/>
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-indigo-500">You Build a Single Line</span>
          </h1>
          <p className="max-w-2xl mx-auto text-lg text-neutral-400 mb-8">
            Enter your startup idea. Our multi-agent competitive pipeline parses details, searches active web listings, classifies and verifies sources, inspects product copy, and builds your interactive strategy dashboard instantly.
          </p>

          <form onSubmit={handleAnalyze} className="max-w-3xl mx-auto bg-[#111111] p-2 rounded-2xl border border-[#222222] shadow-xl shadow-black/50">
            <textarea
              className="w-full bg-transparent px-4 py-3 text-white placeholder-neutral-500 focus:outline-none resize-none h-32 rounded-xl text-lg"
              placeholder="Describe your startup idea in detail... e.g. A micro-SaaS platform providing AI-powered smart irrigation recommendations for commercial urban farms using localized climate data."
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              disabled={loading}
            />
            <div className="flex items-center justify-between p-2 border-t border-[#222222]">
              <div className="text-xs text-neutral-500 px-2">
                Press Analyze to launch Agent pipeline.
              </div>
              <button
                type="submit"
                disabled={loading || !idea.trim()}
                className={`flex items-center space-x-2 px-6 py-3 rounded-xl font-medium transition-all ${
                  loading || !idea.trim()
                    ? 'bg-neutral-800 text-neutral-500 cursor-not-allowed'
                    : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-600/25 hover:shadow-blue-500/35 cursor-pointer'
                }`}
              >
                <span>Analyze Idea</span>
                <ArrowRight className="h-4 w-4" />
              </button>
            </div>
          </form>

          {/* Loading Pipeline Steps */}
          {loading && (
            <div className="max-w-xl mx-auto mt-8 bg-[#111111] border border-[#222222] rounded-xl p-6 text-left">
              <h3 className="text-sm font-semibold uppercase tracking-wider text-neutral-400 mb-4 flex items-center space-x-2">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
                </span>
                <span>Active Agent Pipeline</span>
              </h3>
              <div className="space-y-4">
                {steps.map((step, idx) => {
                  const isActive = idx === loadingStep;
                  const isCompleted = idx < loadingStep;
                  return (
                    <div
                      key={idx}
                      className={`flex items-center space-x-3 transition-colors duration-500 ${
                        isActive ? 'text-white font-medium' : isCompleted ? 'text-neutral-400' : 'text-neutral-600'
                      }`}
                    >
                      <div className={`h-6 w-6 rounded-full flex items-center justify-center text-xs border ${
                        isCompleted 
                          ? 'bg-blue-950 border-blue-500 text-blue-400' 
                          : isActive 
                            ? 'bg-neutral-800 border-neutral-600 text-white animate-pulse' 
                            : 'bg-neutral-900 border-neutral-800 text-neutral-600'
                      }`}>
                        {isCompleted ? <Check className="h-3.5 w-3.5" /> : idx + 1}
                      </div>
                      <span className="text-sm">{step}</span>
                      {isActive && (
                        <span className="text-xs text-blue-500 ml-auto animate-pulse">Running...</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {error && (
            <div className="max-w-xl mx-auto mt-8 bg-red-950/20 border border-red-900/50 rounded-xl p-4 flex items-start space-x-3 text-left">
              <AlertCircle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
              <div>
                <h4 className="text-sm font-semibold text-red-400">Analysis Failed</h4>
                <p className="text-sm text-red-300/80 mt-1">{error}</p>
              </div>
            </div>
          )}
        </section>

        {data && (
          <div className="space-y-12">
            
            {/* SECTION 2: IDEA SUMMARY CARD */}
            <section className="fade-in delay-100">
              <div className="bg-[#111111] border border-[#222222] rounded-2xl p-6 sm:p-8 relative overflow-hidden">
                {/* Decorative glow */}
                <div className="absolute top-0 right-0 w-96 h-96 bg-blue-500/5 rounded-full blur-3xl -z-10"></div>
                
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
                  <div className="flex items-center space-x-3">
                    <Sparkles className="h-6 w-6 text-blue-500" />
                    <h2 className="text-2xl font-bold text-white">Parsed Startup Core</h2>
                  </div>
                  <span className="self-start sm:self-auto px-3 py-1 rounded-full bg-blue-950/60 border border-blue-500/30 text-blue-400 font-medium text-xs tracking-wider uppercase">
                    {data.parsed_idea.domain}
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                  <div className="space-y-4">
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-500 mb-1">Problem Statement</h4>
                      <p className="text-neutral-300 leading-relaxed">{data.parsed_idea.problem}</p>
                    </div>
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-500 mb-1">Proposed Solution</h4>
                      <p className="text-neutral-300 leading-relaxed">{data.parsed_idea.solution}</p>
                    </div>
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-500 mb-1">Target Audience</h4>
                      <p className="text-neutral-300 leading-relaxed">{data.parsed_idea.target_users}</p>
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-500 mb-2">Key Value Pillars</h4>
                      <div className="flex flex-wrap gap-2">
                        {data.parsed_idea.key_features.map((feature, idx) => (
                          <span key={idx} className="text-xs font-medium bg-neutral-900 border border-neutral-800 text-neutral-300 px-3 py-1.5 rounded-lg">
                            {feature}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-neutral-500 mb-1">Unique Positioning Angle</h4>
                      <div className="p-4 rounded-xl bg-blue-950/20 border border-blue-900/30 text-blue-300 text-sm italic">
                        "{data.parsed_idea.unique_angle}"
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            {/* SECTION 3: COMPETITOR CARDS — HORIZONTALLY SCROLLABLE (CHANGE 6) */}
            <section className="fade-in delay-200">
              <div className="mb-6 flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-semibold uppercase tracking-wider text-neutral-400">Identified Competitors</h3>
                  <p className="text-sm text-neutral-500">
                    {data.competitors.length > 0
                      ? `Showing ${data.competitors.length} real competitor${data.competitors.length === 1 ? '' : 's'}`
                      : 'Live competitor products discovered and parsed on the web.'}
                  </p>
                </div>
                {data.competitors.length > 1 && (
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => scrollCompetitors('left')}
                      className="scroll-chevron"
                      aria-label="Scroll left"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => scrollCompetitors('right')}
                      className="scroll-chevron"
                      aria-label="Scroll right"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                )}
              </div>

              {data.competitors.length === 0 ? (
                /* Empty state */
                <div className="bg-[#111111] border border-[#222222] rounded-xl p-8 text-center">
                  <div className="flex justify-center mb-4">
                    <div className="p-3 rounded-full bg-amber-950/30 border border-amber-900/40">
                      <AlertCircle className="h-6 w-6 text-amber-400" />
                    </div>
                  </div>
                  <h4 className="text-base font-semibold text-white mb-2">No live competitor products found</h4>
                  <p className="text-sm text-neutral-400 max-w-md mx-auto">
                    No live competitor products found for this idea — try rephrasing with a more specific feature or comparing to a known tool you're aware of.
                  </p>
                </div>
              ) : (
                <div ref={scrollRef} className="competitor-scroll-row">
                  {data.competitors.map((competitor, idx) => (
                    <div key={idx} className="bg-[#111111] border border-[#222222] rounded-xl p-6 transition-card flex flex-col justify-between">
                      <div>
                        <div className="flex items-start justify-between gap-3 mb-2">
                          <h4 className="text-lg font-bold text-white tracking-tight">{competitor.name}</h4>
                          <a
                            href={competitor.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center text-xs text-blue-400 hover:text-blue-300 transition-colors shrink-0 bg-blue-950/40 border border-blue-900/40 px-2.5 py-1 rounded-lg"
                          >
                            <span className="mr-1.5">{getCleanDomain(competitor.url)}</span>
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        </div>
                        <p className="text-xs text-neutral-400 italic mb-4">"{competitor.tagline}"</p>

                        <div className="mb-6">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500 block mb-2">Core Features</span>
                          <ul className="space-y-2">
                            {(competitor.core_features || []).map((feature, fIdx) => (
                              <li key={fIdx} className="flex items-start space-x-2 text-xs text-neutral-300">
                                <Check className="h-3.5 w-3.5 text-blue-500 shrink-0 mt-0.5" />
                                <span>{feature}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>

                      <div className="border-t border-neutral-900 pt-4 mt-6">
                        <div className="flex items-center justify-between mb-4">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">Pricing Model</span>
                          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-neutral-800 text-neutral-300 border border-neutral-700">
                            {competitor.pricing_model}
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-[11px]">
                          <div>
                            <span className="text-emerald-500 font-semibold block mb-1">Strengths</span>
                            <ul className="space-y-1">
                              {(competitor.strengths || []).map((s, sIdx) => (
                                <li key={sIdx} className="text-neutral-400 truncate">• {s}</li>
                              ))}
                              {(!competitor.strengths || competitor.strengths.length === 0) && (
                                <li className="text-neutral-600 italic">Not stated on website</li>
                              )}
                            </ul>
                          </div>
                          <div>
                            <span className="text-red-500 font-semibold block mb-1">Weaknesses</span>
                            <ul className="space-y-1">
                              {(competitor.weaknesses || []).map((w, wIdx) => (
                                <li key={wIdx} className="text-neutral-400 truncate">• {w}</li>
                              ))}
                              {(!competitor.weaknesses || competitor.weaknesses.length === 0) && (
                                <li className="text-neutral-600 italic">None inferred from text</li>
                              )}
                            </ul>
                          </div>
                        </div>

                        {/* Verify sources toggle (CHANGE 4/6 evidence) */}
                        {competitor.evidence && (
                          <div className="mt-4 border-t border-neutral-900 pt-3">
                            <button
                              onClick={() => toggleEvidence(idx)}
                              className="flex items-center space-x-2 text-[10px] font-semibold uppercase tracking-wider text-neutral-500 hover:text-blue-400 transition-colors w-full cursor-pointer"
                            >
                              {expandedEvidence.has(idx) ? (
                                <EyeOff className="h-3 w-3" />
                              ) : (
                                <Eye className="h-3 w-3" />
                              )}
                              <span>{expandedEvidence.has(idx) ? 'Hide sources' : 'Verify sources'}</span>
                              <Shield className="h-3 w-3 ml-auto text-blue-500/50" />
                            </button>
                            <div className={`verify-section ${expandedEvidence.has(idx) ? 'expanded' : ''}`}>
                              <div className="mt-3 space-y-2">
                                {Object.entries(EVIDENCE_LABELS).map(([key, label]) => {
                                  const snippet = competitor.evidence[key];
                                  if (!snippet) return null;
                                  return (
                                    <div key={key} className="flex items-start gap-2 text-[10px]">
                                      <span className="text-neutral-500 font-semibold shrink-0 w-20 text-right">{label}:</span>
                                      <span className="text-neutral-400 italic">"{snippet}"</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* SECTIONS 4 & 5: POSITIONING CHART & FEATURE MATRIX */}
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-8 fade-in delay-300">
              
              {/* SECTION 4: RADAR CHART */}
              <div className="bg-[#111111] border border-[#222222] rounded-xl p-6 flex flex-col">
                <div className="mb-4">
                  <h3 className="text-lg font-bold text-white">Competitive Positioning</h3>
                  <p className="text-xs text-neutral-500">Comparing dimensions against primary alternatives.</p>
                </div>
                <div className="h-80 w-full flex-grow flex items-center justify-center">
                  <ResponsiveContainer width="100%" height="100%">
                    <RadarChart cx="50%" cy="50%" outerRadius="75%" data={chartData}>
                      <PolarGrid stroke="#222222" />
                      <PolarAngleAxis dataKey="subject" stroke="#888888" tick={{ fontSize: 11 }} />
                      <PolarRadiusAxis angle={30} domain={[0, 100]} stroke="#222222" tick={{ fontSize: 10, fill: '#666666' }} />
                      <Tooltip 
                        contentStyle={{ backgroundColor: '#111111', borderColor: '#222222', borderRadius: '8px', color: '#fff' }}
                        itemStyle={{ fontSize: '12px' }}
                      />
                      <Radar
                        name="Your Idea"
                        dataKey="Your Idea"
                        stroke="#3b82f6"
                        fill="#3b82f6"
                        fillOpacity={0.25}
                      />
                      {data.strategy.competitor_scores.map((comp, idx) => (
                        <Radar
                          key={comp.name}
                          name={comp.name}
                          dataKey={comp.name}
                          stroke={competitorColors[idx % competitorColors.length]}
                          fill={competitorColors[idx % competitorColors.length]}
                          fillOpacity={0.15}
                        />
                      ))}
                      <Legend 
                        wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }}
                        iconSize={8}
                        iconType="circle"
                      />
                    </RadarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* SECTION 5: FEATURE COMPARISON TABLE */}
              <div className="bg-[#111111] border border-[#222222] rounded-xl p-6 flex flex-col">
                <div className="mb-4">
                  <h3 className="text-lg font-bold text-white">Feature Matrix</h3>
                  <p className="text-xs text-neutral-500">Feature gap maps. Opportunities highlighted in light green.</p>
                </div>
                <div className="overflow-x-auto flex-grow">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-[#222222]">
                        <th className="py-3 px-2 text-neutral-400 font-semibold">Feature</th>
                        <th className="py-3 px-2 bg-blue-500/5 text-blue-400 font-bold border-x border-[#222222] text-center w-20">Your Idea</th>
                        {data.competitors.map((c, idx) => (
                          <th key={idx} className="py-3 px-2 text-neutral-300 font-semibold text-center truncate max-w-28">{c.name}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Object.keys(data.strategy.feature_matrix || {}).map((featureName, idx) => {
                        const rowData = data.strategy.feature_matrix[featureName];
                        // Row is a gap if user_idea is true and all competitor properties are false
                        const keys = Object.keys(rowData);
                        const competitorsFalse = keys
                          .filter(k => k !== 'user_idea')
                          .every(k => rowData[k] === false);
                        const isGap = rowData.user_idea && competitorsFalse;

                        return (
                          <tr
                            key={idx}
                            className={`border-b border-[#222222]/50 hover:bg-neutral-800/20 transition-colors ${
                              isGap ? 'bg-emerald-950/20 text-emerald-300' : ''
                            }`}
                          >
                            <td className="py-3 px-2 font-medium text-neutral-200">
                              <span className="block truncate max-w-44 sm:max-w-none">{featureName}</span>
                              {isGap && (
                                <span className="inline-block text-[9px] bg-emerald-500/10 border border-emerald-500/20 px-1 py-0.2 rounded text-emerald-400 font-semibold mt-0.5 uppercase tracking-wider">
                                  Gap Opportunity
                                </span>
                              )}
                            </td>
                            <td className="py-3 px-2 bg-blue-500/5 border-x border-[#222222] text-center">
                              {rowData.user_idea ? (
                                <Check className="h-4 w-4 text-blue-500 mx-auto" />
                              ) : (
                                <X className="h-4 w-4 text-red-500/50 mx-auto" />
                              )}
                            </td>
                            {data.competitors.map((c, cIdx) => {
                              // Safely access matching key in rowData
                              // Key might be competitor's full name, look for exact match or close match
                              const matchingKey = keys.find(k => k.toLowerCase() === c.name.toLowerCase()) || c.name;
                              const val = rowData[matchingKey];
                              return (
                                <td key={cIdx} className="py-3 px-2 text-center">
                                  {val ? (
                                    <Check className="h-4 w-4 text-neutral-400 mx-auto" />
                                  ) : (
                                    <X className="h-4 w-4 text-red-500/20 mx-auto" />
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

            </section>

            {/* SECTION 6: IDEA SCORE DASHBOARD */}
            <section className="fade-in delay-400">
              <div className="mb-6">
                <h3 className="text-lg font-semibold uppercase tracking-wider text-neutral-400">Pillar Scoring</h3>
                <p className="text-sm text-neutral-500">Autonomous evaluation metrics from 0 to 100.</p>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                {[
                  { label: 'Innovation', val: data.strategy.idea_scores.innovation, desc: 'Uniqueness of values' },
                  { label: 'Market Gap', val: data.strategy.idea_scores.market_gap, desc: 'Underserved opportunities' },
                  { label: 'Feasibility', val: data.strategy.idea_scores.feasibility, desc: 'Technical & operational viability' },
                  { label: 'Competition Density', val: data.strategy.idea_scores.competition_density, desc: 'Density of market alternatives' },
                ].map((item, idx) => (
                  <div key={idx} className="bg-[#111111] border border-[#222222] rounded-xl p-5 flex flex-col sm:flex-row items-center sm:justify-between gap-4 text-center sm:text-left transition-card">
                    <div className="space-y-1">
                      <h4 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider">{item.label}</h4>
                      <p className="text-sm font-bold text-white leading-none">{item.val}%</p>
                      <p className="text-[10px] text-neutral-500">{item.desc}</p>
                    </div>
                    <CircularProgressRing score={item.val} size={64} strokeWidth={6} />
                  </div>
                ))}
              </div>
            </section>

            {/* SECTION 7: STRATEGY PANEL */}
            <section className="fade-in delay-500">
              <div className="mb-6">
                <h3 className="text-lg font-semibold uppercase tracking-wider text-neutral-400">Differentiation Recommendations</h3>
                <p className="text-sm text-neutral-500">Strategic actions to expand market wedges and defend positioning.</p>
              </div>

              <div className="space-y-4">
                {[...(data.strategy.suggestions || [])]
                  .sort((a, b) => {
                    const priorityOrder = { High: 1, Medium: 2, Low: 3 };
                    return (priorityOrder[a.priority] || 99) - (priorityOrder[b.priority] || 99);
                  })
                  .map((suggestion, idx) => {
                    let badgeColor = 'bg-blue-950/40 text-blue-400 border-blue-900/50';
                    if (suggestion.priority === 'High') {
                      badgeColor = 'bg-red-950/40 text-red-400 border-red-900/50';
                    } else if (suggestion.priority === 'Medium') {
                      badgeColor = 'bg-amber-950/40 text-amber-400 border-amber-900/50';
                    }

                    return (
                      <div key={idx} className="bg-[#111111] border border-[#222222] rounded-xl p-5 flex items-start space-x-4">
                        <div className={`text-[10px] font-bold tracking-wider uppercase px-2.5 py-1 rounded-lg border ${badgeColor} shrink-0 mt-0.5`}>
                          {suggestion.priority}
                        </div>
                        <div>
                          <h4 className="text-sm font-bold text-white mb-1">{suggestion.title}</h4>
                          <p className="text-xs text-neutral-400 leading-relaxed">{suggestion.description}</p>
                        </div>
                      </div>
                    );
                  })}
              </div>
            </section>

            {/* SECTION 8: EXECUTIVE SUMMARY */}
            <section className="fade-in delay-500">
              <div className="bg-gradient-to-tr from-[#141414] to-[#111111] border border-[#222222] rounded-xl p-6 sm:p-8 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
                <div className="space-y-2 max-w-3xl">
                  <div className="flex items-center space-x-2">
                    <div className="h-1.5 w-1.5 rounded-full bg-blue-500"></div>
                    <span className="text-[10px] font-semibold text-neutral-500 uppercase tracking-wider">Executive Overview</span>
                  </div>
                  <h4 className="text-xl font-bold text-white">Strategic Verdict</h4>
                  <p className="text-sm text-neutral-300 leading-relaxed italic">
                    "{data.strategy.summary}"
                  </p>
                </div>
                <button
                  onClick={handleCopyReport}
                  className="flex items-center space-x-2 bg-neutral-900 border border-neutral-800 hover:border-neutral-700 text-white px-5 py-3 rounded-xl text-xs font-semibold cursor-pointer transition-all self-stretch md:self-auto justify-center hover:bg-neutral-800"
                >
                  <Copy className="h-4 w-4" />
                  <span>{copied ? 'Copied to Clipboard!' : 'Copy Summary Report'}</span>
                </button>
              </div>
            </section>

          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-[#222222] bg-[#080808] py-12 px-6 mt-20 text-center text-xs text-neutral-600">
        <p className="max-w-xl mx-auto mb-2">
          IdeaLens platform parses, validates, and analyzes startups by fetching live websites in real-time. Make sure target domains permit automated crawling.
        </p>
        <p>© 2026 IdeaLens. All rights reserved.</p>
      </footer>
    </div>
  );
}
