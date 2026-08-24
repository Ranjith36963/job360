"use client";

import {
  Briefcase,
  Search,
  FileText,
  GraduationCap,
  Target,
  BarChart3,
  Users,
  Globe,
  Star,
  Award,
  TrendingUp,
  Handshake,
  Building2,
  MapPin,
  Radar,
  Layers,
  Mail,
  Clock,
  CheckCircle,
  Shield,
  Zap,
  BookOpen,
  PenTool,
  Laptop,
  Coffee,
  Lightbulb,
  Rocket,
  Heart,
  Send,
  UserCheck,
  type LucideIcon,
} from "lucide-react";

const ICON_SET: LucideIcon[] = [
  Briefcase, Search, FileText, GraduationCap, Target,
  BarChart3, Users, Globe, Star, Award,
  TrendingUp, Handshake, Building2, MapPin, Radar,
  Layers, Mail, Clock, CheckCircle, Shield,
  Zap, BookOpen, PenTool, Laptop, Coffee,
  Lightbulb, Rocket, Heart, Send, UserCheck,
];

function seededRandom(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return s / 2147483647;
  };
}

function generateIcons(count: number) {
  const rand = seededRandom(42);
  const icons = [];
  const cols = 10;
  const rows = Math.ceil(count / cols);

  for (let i = 0; i < count; i++) {
    const row = Math.floor(i / cols);
    const col = i % cols;
    const baseX = (col / cols) * 100;
    const baseY = (row / rows) * 100;

    icons.push({
      Icon: ICON_SET[i % ICON_SET.length],
      x: baseX + (rand() * 8 - 4),
      y: baseY + (rand() * 6 - 3),
      size: 16 + Math.floor(rand() * 16),
      rotation: Math.floor(rand() * 60 - 30),
      delay: Math.floor(rand() * 12),
    });
  }
  return icons;
}

const ICONS = generateIcons(100);

export function FloatingIcons() {
  return (
    // -z-10, not z-0. This layer is fixed and therefore POSITIONED, and a
    // positioned element paints above non-positioned siblings even at z-0 — so
    // on any page whose content sits in a plain static container, the decoration
    // landed ON TOP of the content. At 3x on /register the icons were legible
    // INSIDE the email and password inputs. The landing and profile pages only
    // escaped because their wrappers happen to carry `relative`, which is a
    // coincidence, not a guard: every future page would have to remember it.
    //
    // Note it is invisible to elementsFromPoint (pointer-events-none), so a
    // hit-test probe reports the input on top while the paint says otherwise.
    // Trust the screenshot here, not the stacking read.
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
    >
      {ICONS.map(({ Icon, x, y, size, rotation, delay }, i) => (
        <div
          key={i}
          className="absolute"
          style={{
            left: `${x}%`,
            top: `${y}%`,
            transform: `rotate(${rotation}deg)`,
            opacity: 0.10,
            animation: `icon-float 20s ease-in-out ${delay}s infinite`,
          }}
        >
          <Icon
            style={{ width: size, height: size }}
            strokeWidth={1.5}
            className="text-primary"
          />
        </div>
      ))}
    </div>
  );
}
