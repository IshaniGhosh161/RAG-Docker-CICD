@description('The Azure region for all resources.')
param location string = resourceGroup().location

@description('Short environment name used in resource names.')
param environmentName string = 'ragchat'

@description('Globally unique Azure Container Registry name.')
param acrName string

@description('AKS cluster name.')
param aksClusterName string = 'aks-${environmentName}'

@description('Log Analytics workspace name.')
param logAnalyticsWorkspaceName string = 'law-${environmentName}'

@description('Azure Managed Grafana instance name.')
param grafanaName string = 'grafana-${environmentName}'

@description('The existing MongoDB connection string to keep using for the same app database.')
@secure()
param mongoConnectionString string

@description('Ollama API key if you are using the hosted Ollama service.')
@secure()
param ollamaApiKey string = ''

@description('Tavily API key used by web-search retrieval.')
@secure()
param tavilyApiKey string = ''

@description('The Ollama host accessible from the cluster.')
param ollamaHost string = 'http://ollama:11434'

@description('The exact Ollama model that must remain in use.')
param ollamaModel string = 'gpt-oss:20b-cloud'

@description('AKS node count.')
param nodeCount int = 2

@description('AKS node VM size.')
param vmSize string = 'Standard_D4s_v5'

@description('Kubernetes version to use on AKS. Use a version supported in the target Azure region and API version.')
param kubernetesVersion string = '1.28'

var acrSku = 'Basic'
var tags = {
  application: 'rag-chat'
  environment: environmentName
  managedBy: 'bicep'
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: acrSku
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource aks 'Microsoft.ContainerService/managedClusters@2023-06-01' = {
  name: aksClusterName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Base'
    tier: 'Free'
  }
  properties: {
    dnsPrefix: 'ragchat-${environmentName}'
    kubernetesVersion: kubernetesVersion
    enableRBAC: true
    nodeResourceGroup: '${resourceGroup().name}-aksnodes'
    agentPoolProfiles: [
      {
        name: 'systempool'
        count: nodeCount
        vmSize: vmSize
        osType: 'Linux'
        mode: 'System'
        type: 'VirtualMachineScaleSets'
        maxPods: 110
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      loadBalancerSku: 'standard'
      networkPolicy: 'azure'
    }
    addonProfiles: {
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalytics.id
        }
      }
    }
    apiServerAccessProfile: {
      enablePrivateCluster: false
    }
    // Azure Workload Identity is disabled because this deployment does not configure an OIDC issuer.
    // Enabling it requires a valid AKS OIDC issuer and is not necessary for this repo's current deployment model.
    securityProfile: {
      workloadIdentity: {
        enabled: false
      }
    }
  }
}

resource managedGrafana 'Microsoft.Dashboard/grafana@2022-08-01' = {
  name: grafanaName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

// Grant AKS managed identity pull access to ACR so the cluster can pull the app image without manual portal changes.
resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, aks.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: aks.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output aksName string = aks.name
output aksResourceId string = aks.id
output aksPrincipalId string = aks.identity.principalId
output managedGrafanaName string = managedGrafana.name
output logAnalyticsWorkspaceId string = logAnalytics.id
output mongoConnectionString string = mongoConnectionString
output ollamaHost string = ollamaHost
output ollamaModel string = ollamaModel
output ollamaApiKey string = ollamaApiKey
output tavilyApiKey string = tavilyApiKey
