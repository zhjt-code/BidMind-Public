import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { setNavigate } from '@/services/httpClient'

/**
 * Registers react-router's navigate function with the api module
 * so 401 responses can redirect without a full page reload.
 */
const NavigateSetter: React.FC = () => {
  const navigate = useNavigate()
  useEffect(() => {
    setNavigate(navigate)
  }, [navigate])
  return null
}

export default NavigateSetter
