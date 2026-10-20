import React from 'react'
import FinalAction from '@/components/public/FinalAction'
import HeroSection from '@/components/public/HeroSection'
import PlatformKnowledgeSection from '@/components/public/PlatformKnowledgeSection'
import Reveal from '@/components/public/Reveal'
import ScenarioSection from '@/components/public/ScenarioSection'
import TaskExperience from '@/components/public/TaskExperience'
import TrustAndDataBoundary from '@/components/public/TrustAndDataBoundary'
import TrustMetricsMarquee from '@/components/public/TrustMetricsMarquee'
import UnifiedPlatform from '@/components/public/UnifiedPlatform'

const HomePage: React.FC = () => {
  return (
    <>
      <HeroSection />
      <Reveal>
        <TaskExperience />
      </Reveal>
      <Reveal>
        <UnifiedPlatform />
      </Reveal>
      <Reveal>
        <ScenarioSection />
      </Reveal>
      <Reveal>
        <PlatformKnowledgeSection />
      </Reveal>
      <Reveal>
        <TrustAndDataBoundary />
      </Reveal>
      <Reveal>
        <TrustMetricsMarquee />
      </Reveal>
      <FinalAction />
    </>
  )
}

export default HomePage
